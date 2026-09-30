"""Signed template manifests for the Astrocoda CLI.

A template manifest maps every file that may be shipped to its SHA-256 hash and
is signed with the seller's private key.  Before scaffolding, the CLI:

1. loads ``<template>/astrocoda.manifest.json``,
2. verifies the manifest signature with the embedded public key,
3. recomputes every listed file's hash (any mismatch aborts),
4. copies *only* the listed files (a whitelist: an attacker who drops an extra
   file into a template simply does not get it shipped).

This turns ``astrocoda init`` into a supply-chain check, not a copy job: the
buyer gets exactly the tree the seller signed, no matter which copy or mirror
of the template they point the CLI at.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from astrocoda.license_key import (
    _b64url_decode,
    _b64url_encode,
    LicenseKeyError,
    load_private_key,
    load_public_key,
)
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

MANIFEST_NAME: str = "astrocoda.manifest.json"
MANIFEST_SCHEMA: int = 1


class TemplateIntegrityError(Exception):
    """A template failed signature or hash verification."""


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload["files"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(
    root: Path,
    *,
    exclude: frozenset[str],
    version: str = "0.0.0",
) -> dict:
    """Build an unsigned manifest for every file under ``root`` not excluded."""
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = set(Path(rel).parts)
        if parts & exclude or rel in (MANIFEST_NAME,):
            continue
        files[rel] = sha256_bytes(path.read_bytes())

    if not files:
        raise TemplateIntegrityError("Template directory contains no files to sign.")

    return {
        "schema": MANIFEST_SCHEMA,
        "version": version,
        "files": files,
    }


def sign_manifest(
    manifest: dict,
    private_key: ed25519.Ed25519PrivateKey,
) -> dict:
    """Return a copy of the manifest with ``signature`` attached."""
    signed = dict(manifest)
    signed["signature"] = _b64url_encode(private_key.sign(_canonical(manifest)))
    return signed


def write_signed_manifest(
    root: Path,
    *,
    private_key_pem: bytes,
    exclude: frozenset[str],
    version: str = "0.0.0",
) -> Path:
    """Build, sign and write the manifest into a template root."""
    private_key = load_private_key(private_key_pem)
    manifest = sign_manifest(
        build_manifest(root, exclude=exclude, version=version),
        private_key,
    )
    out = root / MANIFEST_NAME
    # newline="\n" so the manifest is byte-identical whether it is signed on
    # Windows or Linux. The signature covers a canonical re-serialisation of
    # `files` rather than these bytes, so this is about reproducible output
    # rather than correctness.
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    return out


def verify_manifest_signature(
    public_key: ed25519.Ed25519PublicKey,
    manifest: dict,
) -> None:
    """Verify the manifest's signature; raise on failure."""
    signature = manifest.get("signature")
    if not isinstance(signature, str):
        raise TemplateIntegrityError("Manifest is missing its signature.")
    try:
        public_key.verify(_b64url_decode(signature), _canonical(manifest))
    except (InvalidSignature, ValueError, LicenseKeyError) as exc:
        raise TemplateIntegrityError(
            "Template manifest signature is invalid. This template is not the "
            "seller's signed tree; refusing to scaffold."
        ) from exc


def verify_template(
    public_key: ed25519.Ed25519PublicKey,
    root: Path,
) -> dict:
    """Check the manifest in ``root``: signature valid, every listed file
    present and byte-for-byte matching. Return the ``files`` whitelist."""
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists():
        raise TemplateIntegrityError(
            f"Template has no signed manifest ({MANIFEST_NAME}). "
            "Refusing to scaffold an untrusted template."
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateIntegrityError("Template manifest is corrupt JSON.") from exc

    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise TemplateIntegrityError("Template manifest has an unknown schema version.")

    files = manifest.get("files")
    if not isinstance(files, dict):
        raise TemplateIntegrityError("Template manifest has no file list.")

    verify_manifest_signature(public_key, manifest)

    for rel in files:
        candidate = (root / rel).resolve()
        # Guard against path traversal ("../../etc/passwd" in the manifest).
        if not candidate.is_relative_to(root.resolve()):
            raise TemplateIntegrityError(f"Manifest entry escapes the template: {rel!r}")

    for rel, expected in sorted(files.items()):
        candidate = root / rel
        if not candidate.is_file():
            raise TemplateIntegrityError(
                f"Template is missing a signed file: {rel}. "
                "The tree does not match the seller's manifest."
            )
        actual = sha256_bytes(candidate.read_bytes())
        if actual != expected:
            raise TemplateIntegrityError(
                f"Template file {rel} does not match the signed manifest "
                f"(hash changed). The tree is tampered or stale."
            )

    return files


def copy_verified(root: Path, target: Path, files: dict) -> None:
    """Whitelist copy: only files listed in the manifest reach the target."""
    target.mkdir(parents=True, exist_ok=False)
    for rel in sorted(files):
        src = root / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())