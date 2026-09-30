"""Offline license key verification for the Astrocoda CLI.

A license key is a signed payload:

    astrocoda_<base64url(json payload)>.<base64url(ed25519 signature)>

The payload carries ``email``, ``plan`` and ``exp`` (unix timestamp).  The
signature is made with the seller's private key; the CLI only ever holds the
matching public key.  Because ``exp`` lives inside the signed payload, a buyer
cannot rewrite an expired key without invalidating the signature.

No server is involved: verification happens entirely on the buyer's machine,
which is what makes the license work for a seller with no infrastructure.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding as Encodings,
    PublicFormat as PublicFormats,
    load_pem_private_key,
    load_pem_public_key,
)

KEY_PREFIX: str = "astrocoda_"


class LicenseKeyError(Exception):
    """A license key failed to parse or verify."""


@dataclass(frozen=True)
class LicenseClaims:
    email: str
    plan: str
    expires_at: datetime

    def as_dict(self) -> dict:
        return {
            "email": self.email,
            "plan": self.plan,
            "expires_at": self.expires_at.isoformat(),
        }


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_key(
    private_key: ed25519.Ed25519PrivateKey,
    *,
    email: str,
    plan: str,
    expires_at: datetime,
) -> str:
    """Sign a license key for a buyer. Private-key API, seller only."""
    payload = json.dumps(
        {"email": email, "plan": plan, "exp": int(expires_at.timestamp())},
        separators=(",", ":"),
    ).encode("utf-8")
    signature = private_key.sign(payload)
    return f"{KEY_PREFIX}{_b64url_encode(payload)}.{_b64url_encode(signature)}"


def verify_key(public_key: ed25519.Ed25519PublicKey, key: str) -> LicenseClaims:
    """Verify a license key locally; raise :class:`LicenseKeyError` on failure."""
    if not key.startswith(KEY_PREFIX):
        raise LicenseKeyError("Malformed license key (missing prefix).")

    body = key[len(KEY_PREFIX) :]
    if "." not in body:
        raise LicenseKeyError("Malformed license key (missing signature).")
    payload_b64, signature_b64 = body.split(".", 1)

    try:
        payload = _b64url_decode(payload_b64)
        signature = _b64url_decode(signature_b64)
    except (ValueError, base64.binascii.Error) as exc:
        raise LicenseKeyError("Malformed license key (bad base64).") from exc

    try:
        public_key.verify(signature, payload)
    except InvalidSignature as exc:
        raise LicenseKeyError("License key signature is invalid.") from exc

    try:
        claims = json.loads(payload)
        email = str(claims["email"])
        plan = str(claims["plan"])
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LicenseKeyError("License key payload is malformed.") from exc

    if not email or not plan:
        raise LicenseKeyError("License key payload is missing identity.")

    if expires_at <= datetime.now(timezone.utc):
        raise LicenseKeyError("License key has expired.")

    return LicenseClaims(email=email, plan=plan, expires_at=expires_at)


def load_public_key(pem: bytes) -> ed25519.Ed25519PublicKey:
    """Load a PEM-encoded Ed25519 public key."""
    loaded = load_pem_public_key(pem)
    if not isinstance(loaded, ed25519.Ed25519PublicKey):
        raise LicenseKeyError("Key is not an Ed25519 public key.")
    return loaded


def load_private_key(pem: bytes) -> ed25519.Ed25519PrivateKey:
    """Load a PEM-encoded Ed25519 private key."""
    loaded = load_pem_private_key(pem, password=None)
    if not isinstance(loaded, ed25519.Ed25519PrivateKey):
        raise LicenseKeyError("Key is not an Ed25519 private key.")
    return loaded


def public_key_fingerprint(public_key: ed25519.Ed25519PublicKey) -> str:
    """Short hex id of the embedded public key, for CLI/buyer confirmation."""
    raw = public_key.public_bytes(
        Encodings.Raw,
        PublicFormats.Raw,
    )
    return hashlib.sha256(raw).hexdigest()[:12]