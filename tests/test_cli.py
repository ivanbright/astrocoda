"""Hermetic tests for the astrocoda CLI: signed-key licensing, gate, scaffold.

No network and no license server: a fresh Ed25519 keypair is generated in a
fixture, and the CLI is pointed at a temp public key.  Valid keys, tampered
keys and expired keys are all exercised locally.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from astrocoda.cli import build_parser, main
from astrocoda.config import PUBLIC_KEY_PATH
from astrocoda.credentials import (
    Credentials,
    NoCredentialsError,
    drop_credentials,
    load_credentials,
    save_credentials,
)
from astrocoda.license_key import LicenseKeyError, issue_key, verify_key
from astrocoda.operations import require_license

FUTURE = (datetime.now(timezone.utc) + timedelta(days=30))
PAST = (datetime.now(timezone.utc) - timedelta(days=1))


class KeyPair:
    def __init__(self) -> None:
        self.private = ed25519.Ed25519PrivateKey.generate()
        self.public = self.private.public_key()

    def issue(self, email: str = "buyer@example.com", plan: str = "starter", expires_at: datetime = FUTURE) -> str:
        return issue_key(self.private, email=email, plan=plan, expires_at=expires_at)

    def write_public(self, path: Path) -> None:
        path.write_bytes(
            self.public.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        )


@pytest.fixture
def keypair(tmp_path) -> KeyPair:
    pair = KeyPair()
    return pair


@pytest.fixture
def license_env(keypair, tmp_path, monkeypatch):
    """Point the CLI at a temp public key + temp credential home."""
    public_file = tmp_path / "license_public.pem"
    keypair.write_public(public_file)
    monkeypatch.setattr("astrocoda.config.PUBLIC_KEY_PATH", public_file)
    monkeypatch.setattr("astrocoda.cli.PUBLIC_KEY_PATH", public_file)
    monkeypatch.setattr("astrocoda.operations.PUBLIC_KEY_PATH", public_file)
    monkeypatch.setenv("ASTROCODA_HOME", str(tmp_path / ".astrocoda"))
    return tmp_path


# ---------------------------------------------------------------------------
# License key signing / verification
# ---------------------------------------------------------------------------
def test_valid_key_verifies_locally(keypair):
    key = keypair.issue()
    claims = verify_key(keypair.public, key)
    assert claims.email == "buyer@example.com"
    assert claims.plan == "starter"
    assert abs((claims.expires_at - FUTURE).total_seconds()) < 5


def test_tampered_payload_rejected(keypair):
    key = keypair.issue()
    body = key.split("_", 1)[1]
    payload_b64, sig = body.split(".", 1)
    # flip a base64 char in the payload without touching the signature
    tampered_payload = ("A" if payload_b64[0] != "A" else "B") + payload_b64[1:]
    tampered = f"astrocoda_{tampered_payload}.{sig}"
    with pytest.raises(LicenseKeyError):
        verify_key(keypair.public, tampered)


def test_replaced_signature_rejected(keypair):
    other = KeyPair()
    key = keypair.issue()
    body = key.split("_", 1)[1]
    payload_b64, _ = body.split(".", 1)
    forged_sig = other.private.sign(b"x")
    from astrocoda.license_key import _b64url_encode

    forged = f"astrocoda_{payload_b64}.{_b64url_encode(forged_sig)}"
    with pytest.raises(LicenseKeyError):
        verify_key(keypair.public, forged)


def test_expired_key_rejected(keypair):
    key = keypair.issue(expires_at=PAST)
    with pytest.raises(LicenseKeyError, match="expired"):
        verify_key(keypair.public, key)


# ---------------------------------------------------------------------------
# Credential store
# ---------------------------------------------------------------------------
def test_save_and_load_round_trip(license_env, keypair):
    key = keypair.issue()
    save_credentials(
        Credentials(license_key=key, email="buyer@example.com", plan="starter", expires_at=FUTURE)
    )
    assert load_credentials().email == "buyer@example.com"


def test_missing_credentials_raise(license_env):
    with pytest.raises(NoCredentialsError):
        load_credentials()


def test_drop_credentials(license_env, keypair):
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))
    assert drop_credentials() is True
    assert drop_credentials() is False


# ---------------------------------------------------------------------------
# License gate (offline)
# ---------------------------------------------------------------------------
def test_require_license_succeeds_locally(license_env, keypair):
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))
    session = require_license()
    assert session.email == "buyer@example.com"
    assert session.plan == "starter"


def test_require_license_with_no_credentials_fails(license_env):
    with pytest.raises(SystemExit) as exc:
        require_license()
    assert exc.value.code == 1


def test_require_license_rejects_forged_store(license_env, keypair):
    """Editing credentials.json buys nothing: the signature check fails."""
    other = KeyPair()
    forged_key = other.issue()
    save_credentials(Credentials(license_key=forged_key, email="e", plan="p", expires_at=FUTURE))
    with pytest.raises(SystemExit) as exc:
        require_license()
    assert exc.value.code == 1


def test_require_license_uses_signed_expiry_not_json(license_env, keypair):
    """Forcing expires_at far in the future in the JSON is ignored: the gate
    re-verifies the raw key locally, and expiry comes from the signed payload."""
    key = keypair.issue(expires_at=FUTURE)
    home = Path(license_env) / ".astrocoda"
    f = home / "credentials.json"
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))
    data = json.loads(f.read_text(encoding="utf-8"))
    data["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=9999)).isoformat()
    f.write_text(json.dumps(data), encoding="utf-8")
    session = require_license()
    assert abs((session.expires_at_as_datetime - FUTURE).total_seconds()) < 5


def test_signature_tampering_cannot_pass_gate(license_env, keypair):
    """Modifying the signed key itself (its payload) must fail the gate."""
    other = KeyPair()
    key = keypair.issue()
    body = key.split("_", 1)[1]
    payload_b64, sig = body.split(".", 1)
    tampered_payload = ("A" if payload_b64[0] != "A" else "B") + payload_b64[1:]
    tampered = f"astrocoda_{tampered_payload}.{sig}"
    save_credentials(Credentials(license_key=tampered, email="e", plan="p", expires_at=FUTURE))
    with pytest.raises(SystemExit) as exc:
        require_license()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------
def test_login_stores_credentials(license_env, keypair, capsys):
    key = keypair.issue()
    rc = main(["login", key])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Logged in" in out
    assert load_credentials().email == "buyer@example.com"


def test_login_rejects_forged_key(license_env, capsys):
    other = KeyPair()
    forged = other.issue()
    rc = main(["login", forged])
    assert rc == 1
    assert "rejected" in capsys.readouterr().out
    with pytest.raises(NoCredentialsError):
        load_credentials()


def test_status_prints_credentials(license_env, keypair, capsys):
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="buyer@example.com", plan="starter", expires_at=FUTURE))
    rc = main(["status"])
    assert rc == 0
    assert "buyer@example.com" in capsys.readouterr().out


def test_logout_clears_credentials(license_env, keypair):
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))
    rc = main(["logout"])
    assert rc == 0
    with pytest.raises(NoCredentialsError):
        load_credentials()


def test_init_without_credentials_fails_closed(license_env, tmp_path):
    assert main(["init", str(tmp_path / "proj")]) == 1


@pytest.fixture
def signed_template(keypair, tmp_path):
    """A small template root carrying a manifest signed by the keypair."""
    template = tmp_path / "template"
    template.mkdir()
    (template / "bootstrap.py").write_text("print('ok')\n", encoding="utf-8")
    (template / "app").mkdir()
    (template / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")

    from astrocoda.manifest import write_signed_manifest
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    private_pem = keypair.private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    write_signed_manifest(
        template,
        private_key_pem=private_pem,
        exclude=frozenset(),
    )
    return template


def test_init_with_unsigned_template_is_refused(license_env, keypair, tmp_path):
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))
    template = tmp_path / "bare"
    template.mkdir()
    (template / "file.txt").write_text("data", encoding="utf-8")

    rc = main(["init", str(tmp_path / "proj"), "--source", str(template)])
    assert rc == 1
    assert not (tmp_path / "proj").exists()


def test_init_with_valid_license_scaffolds_signed_tree(license_env, keypair, signed_template, tmp_path, capsys):
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))
    rc = main(["init", str(tmp_path / "proj"), "--source", str(signed_template)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Template verified against seller signature" in out
    assert (tmp_path / "proj" / "bootstrap.py").exists()
    assert (tmp_path / "proj" / "app" / "main.py").exists()


def test_init_refuses_tampered_file(license_env, keypair, signed_template, tmp_path):
    """Editing a listed file after signing must abort before any copy."""
    (signed_template / "app" / "main.py").write_text("x = 999\n", encoding="utf-8")
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))

    rc = main(["init", str(tmp_path / "proj"), "--source", str(signed_template)])
    assert rc == 1
    assert not (tmp_path / "proj").exists()


def test_init_refuses_manifest_signed_by_other_key(license_env, keypair, tmp_path):
    """A manifest signed with a *different* private key must fail."""
    other = KeyPair()
    template = tmp_path / "template"
    template.mkdir()
    (template / "file.txt").write_text("data", encoding="utf-8")

    from astrocoda.manifest import write_signed_manifest
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    private_pem = other.private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    write_signed_manifest(template, private_key_pem=private_pem, exclude=frozenset())

    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))
    rc = main(["init", str(tmp_path / "proj"), "--source", str(template)])
    assert rc == 1
    assert not (tmp_path / "proj").exists()


def test_init_whitelist_drops_unknown_files(license_env, keypair, signed_template, tmp_path):
    """Files dropped into the template after signing must NOT be copied."""
    (signed_template / "evil.sh").write_text("rm -rf /\n", encoding="utf-8")
    key = keypair.issue()
    save_credentials(Credentials(license_key=key, email="e", plan="p", expires_at=FUTURE))

    rc = main(["init", str(tmp_path / "proj"), "--source", str(signed_template)])
    assert rc == 0
    assert not (tmp_path / "proj" / "evil.sh").exists()
    assert (tmp_path / "proj" / "bootstrap.py").exists()


def test_all_commands_are_registered(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    help_text = capsys.readouterr().out
    for name in ("login", "status", "logout", "init", "up"):
        assert name in help_text