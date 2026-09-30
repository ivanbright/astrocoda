"""Hermetic tests for the astrocoda CLI: signing, ungated scaffold, commands.

No network and no account: a fresh Ed25519 keypair is generated in a fixture and
the CLI is pointed at a temp public key.

The licence-key tests are retained even though nothing is gated any more -- the
signing format is still what backs the manifest signature, and keeping it tested
means a paid tier can be switched back on safely.  The tests that matter most
here are the supply-chain ones: ``init`` must refuse any template it cannot
verify against the seller's signature, and that holds with no login at all.
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
from astrocoda.identify import Identity, load_identity
from astrocoda.license_key import LicenseKeyError, issue_key, verify_key

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
def env(keypair, tmp_path, monkeypatch):
    """Point the CLI at a temp public key and a temp home directory."""
    public_file = tmp_path / "license_public.pem"
    keypair.write_public(public_file)
    monkeypatch.setattr("astrocoda.config.PUBLIC_KEY_PATH", public_file)
    monkeypatch.setattr("astrocoda.operations.PUBLIC_KEY_PATH", public_file)
    monkeypatch.setenv("ASTROCODA_HOME", str(tmp_path / ".astrocoda"))
    return tmp_path


# ---------------------------------------------------------------------------
# License key signing / verification (format retained, nothing gated on it)
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
# CLI commands
# ---------------------------------------------------------------------------
def test_init_needs_no_credentials(env, tmp_path):
    """No account, no key, no stored state: the gate is gone."""
    assert load_identity() is None
    assert main(["init", str(tmp_path / "proj"), "--no-email"]) == 1
    assert not (tmp_path / "proj").exists()


def test_identify_without_address_shares_nothing(env, capsys):
    assert main(["identify"]) == 0
    assert "Nothing shared" in capsys.readouterr().out
    assert load_identity() is None


def test_identify_rejects_malformed_address(env, capsys, monkeypatch):
    monkeypatch.setattr("astrocoda.cli.submit", lambda *a, **k: True)
    assert main(["identify", "not-an-email"]) == 1
    assert "does not look like" in capsys.readouterr().out
    assert load_identity() is None


def test_identify_stores_valid_address(env, monkeypatch):
    monkeypatch.setattr("astrocoda.cli.submit", lambda *a, **k: True)
    assert main(["identify", "Buyer@Example.com"]) == 0
    assert load_identity().email == "buyer@example.com"


def test_status_reports_version_and_nothing_shared(env, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "astrocoda" in out
    assert "not shared" in out


def test_status_reports_declined(env, capsys):
    from astrocoda.identify import save_identity

    save_identity(Identity(declined=True))
    assert main(["status"]) == 0
    assert "declined" in capsys.readouterr().out


def test_optout_with_nothing_shared(env, capsys):
    assert main(["optout"]) == 0
    assert "Nothing was shared" in capsys.readouterr().out


def test_optout_forgets_and_unsubscribes(env, monkeypatch):
    from astrocoda.identify import save_identity

    seen: list[str] = []
    monkeypatch.setattr(
        "astrocoda.cli.unsubscribe",
        lambda endpoint, email, **k: seen.append(email) or True,
    )
    save_identity(Identity(email="buyer@example.com"))
    assert main(["optout"]) == 0
    assert seen == ["buyer@example.com"]
    assert load_identity() is None


def test_optout_local_only_skips_the_server(env, monkeypatch):
    from astrocoda.identify import save_identity

    def boom(*a, **k):
        raise AssertionError("contacted the server despite --local-only")

    monkeypatch.setattr("astrocoda.cli.unsubscribe", boom)
    save_identity(Identity(email="buyer@example.com"))
    assert main(["optout", "--local-only"]) == 0
    assert load_identity() is None


def test_up_reports_missing_compose_file(env, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["up", "--dir", str(empty)]) == 1
    assert "No docker-compose.yml" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Template integrity: the supply-chain gate that actually matters
# ---------------------------------------------------------------------------
@pytest.fixture
def signed_template(keypair, tmp_path):
    """A small template root carrying a manifest signed by the keypair."""
    template = tmp_path / "template"
    template.mkdir()
    (template / "bootstrap.py").write_text("print('ok')\n", encoding="utf-8")
    (template / "app").mkdir()
    (template / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")

    from astrocoda.manifest import write_signed_manifest

    private_pem = keypair.private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    write_signed_manifest(
        template,
        private_key_pem=private_pem,
        exclude=frozenset(),
    )
    return template


def test_init_with_unsigned_template_is_refused(env, keypair, tmp_path):
    template = tmp_path / "bare"
    template.mkdir()
    (template / "file.txt").write_text("data", encoding="utf-8")

    rc = main(["init", str(tmp_path / "proj"), "--source", str(template), "--no-email"])
    assert rc == 1
    assert not (tmp_path / "proj").exists()


def test_init_scaffolds_signed_tree_with_no_license(env, keypair, signed_template, tmp_path, capsys):
    rc = main(["init", str(tmp_path / "proj"), "--source", str(signed_template), "--no-email"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Template verified against seller signature" in out
    assert (tmp_path / "proj" / "bootstrap.py").exists()
    assert (tmp_path / "proj" / "app" / "main.py").exists()


def test_init_refuses_tampered_file(env, keypair, signed_template, tmp_path):
    """Editing a listed file after signing must abort before any copy."""
    (signed_template / "app" / "main.py").write_text("x = 999\n", encoding="utf-8")

    rc = main(["init", str(tmp_path / "proj"), "--source", str(signed_template), "--no-email"])
    assert rc == 1
    assert not (tmp_path / "proj").exists()


def test_init_refuses_manifest_signed_by_other_key(env, keypair, tmp_path):
    """A manifest signed with a *different* private key must fail."""
    other = KeyPair()
    template = tmp_path / "template"
    template.mkdir()
    (template / "file.txt").write_text("data", encoding="utf-8")

    from astrocoda.manifest import write_signed_manifest

    private_pem = other.private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    write_signed_manifest(template, private_key_pem=private_pem, exclude=frozenset())

    rc = main(["init", str(tmp_path / "proj"), "--source", str(template), "--no-email"])
    assert rc == 1
    assert not (tmp_path / "proj").exists()


def test_init_whitelist_drops_unknown_files(env, keypair, signed_template, tmp_path):
    """Files dropped into the template after signing must NOT be copied."""
    (signed_template / "evil.sh").write_text("rm -rf /\n", encoding="utf-8")

    rc = main(["init", str(tmp_path / "proj"), "--source", str(signed_template), "--no-email"])
    assert rc == 0
    assert not (tmp_path / "proj" / "evil.sh").exists()
    assert (tmp_path / "proj" / "bootstrap.py").exists()


def test_init_refuses_existing_target(env, keypair, signed_template, tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()
    rc = main(["init", str(target), "--source", str(signed_template), "--no-email"])
    assert rc == 1
    assert "already exists" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Parser surface
# ---------------------------------------------------------------------------
def test_all_commands_are_registered(capsys):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    help_text = capsys.readouterr().out
    for name in ("init", "identify", "status", "optout", "up"):
        assert name in help_text


def test_license_commands_are_gone(capsys):
    """The paid gate was removed; these must not linger in help output."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    help_text = capsys.readouterr().out
    for name in ("login", "logout"):
        assert name not in help_text


def test_init_exposes_email_flags():
    parser = build_parser()
    args = parser.parse_args(["init", "proj", "--email", "a@example.com", "--no-email"])
    assert args.email == "a@example.com"
    assert args.no_email is True


def test_default_email_flags_are_off():
    parser = build_parser()
    args = parser.parse_args(["init", "proj"])
    assert args.email is None
    assert args.no_email is False
