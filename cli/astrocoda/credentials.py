"""Local license storage for the Astrocoda CLI.

A logged-in CLI stores the raw signed license key and the claims decoded from
it.  Validity is re-established from the signature on every use (no network),
so editing the stored JSON is useless: any tamper breaks the signature, and
``exp`` is part of the signed payload.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class NoCredentialsError(Exception):
    """No license key has been stored for this machine."""


@dataclass(frozen=True)
class Credentials:
    license_key: str
    email: str
    plan: str
    expires_at: datetime

    @classmethod
    def from_dict(cls, data: dict) -> Credentials:
        return cls(
            license_key=data["license_key"],
            email=data["email"],
            plan=data["plan"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )

    def to_dict(self) -> dict:
        return {
            "license_key": self.license_key,
            "email": self.email,
            "plan": self.plan,
            "expires_at": self.expires_at.isoformat(),
        }


def _credentials_file() -> Path:
    return Path(os.environ.get("ASTROCODA_HOME", str(Path.home() / ".astrocoda"))) / "credentials.json"


def save_credentials(credentials: Credentials, path: Path | None = None) -> None:
    """Persist credentials with user-only permissions where supported."""
    path = path or _credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(credentials.to_dict(), indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_credentials(path: Path | None = None) -> Credentials:
    """Read stored credentials, raising when absent or unreadable.

    Note: this only reads the *stored snapshot* of the claims.  The CLI caller
    re-verifies the raw ``license_key`` signature before any gated command, so
    a locally fabricated or doctored file is rejected there.
    """
    path = path or _credentials_file()
    if not path.exists():
        raise NoCredentialsError("Not logged in. Run: astrocoda login <key>")
    try:
        return Credentials.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise NoCredentialsError("Stored credentials are unreadable; run astrocoda login again.") from exc


def drop_credentials(path: Path | None = None) -> bool:
    """Remove stored credentials, returning True when something was deleted."""
    path = path or _credentials_file()
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return True