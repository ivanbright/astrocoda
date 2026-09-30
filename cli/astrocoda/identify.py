"""Optional email collection for the Astrocoda CLI.

Asking for an address is a courtesy, not a gate: the boilerplate is MIT licensed
and downloads the same either way.  Everything in this module is best effort and
must never be able to stop a scaffold, so :func:`submit` swallows its own
failures and the caller continues regardless of the result.

Three rules drive the design:

* Only ask a human who can actually answer.  A non-interactive run (CI, a piped
  shell, ``uvx`` in a script) must not block on ``y/N``, so the prompt is
  skipped when stdin is not a TTY and no email is sent.
* Default to silence.  Bare Enter, ``--no-email``, or ``ASTROCODA_NO_EMAIL=1``
  all mean no, and the choice is remembered so nobody is nagged twice.
* No account, no password, no identifier.  The address is the entire payload.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from astrocoda import __version__
from astrocoda.config import OPTIN_ENDPOINT

MAX_EMAIL_LENGTH = 254

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

PROMPT_TEXT = "Share your email to get update news? [y/N] "


class IdentityError(Exception):
    """The stored identity file could not be read or written."""


@dataclass(frozen=True)
class Identity:
    """What this machine has told us about itself. Never required."""

    email: str | None = None
    declined: bool = False
    asked_at: str | None = None

    @property
    def opted_in(self) -> bool:
        return self.email is not None

    @property
    def answered(self) -> bool:
        return self.asked_at is not None

    @classmethod
    def from_dict(cls, data: dict) -> Identity:
        email = data.get("email")
        return cls(
            email=str(email) if email else None,
            declined=bool(data.get("declined", False)),
            asked_at=data.get("asked_at"),
        )

    def to_dict(self) -> dict:
        return {"email": self.email, "declined": self.declined, "asked_at": self.asked_at}


def identity_file() -> Path:
    """Location of the local identity record, honouring ``ASTROCODA_HOME``."""
    home = os.environ.get("ASTROCODA_HOME") or str(Path.home() / ".astrocoda")
    return Path(home) / "identity.json"


def load_identity(path: Path | None = None) -> Identity | None:
    """Return the stored identity, or ``None`` if this machine never answered."""
    target = path or identity_file()
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return Identity.from_dict(data)


def save_identity(identity: Identity, path: Path | None = None) -> None:
    """Persist the identity record, user-readable only where supported.

    ``asked_at`` is stamped automatically when the caller leaves it unset, so
    no caller has to remember to record that a question was settled.
    """
    target = path or identity_file()
    if identity.asked_at is None:
        identity = Identity(
            email=identity.email,
            declined=identity.declined,
            asked_at=_now(),
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(identity.to_dict(), indent=2), encoding="utf-8")
        os.chmod(target, 0o600)
    except OSError as exc:
        raise IdentityError(f"could not write {target}: {exc}") from exc


def is_interactive() -> bool:
    """True only when both ends are a terminal, so piped runs never block."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def normalise_email(raw: str) -> str | None:
    """Lowercase and sanity-check an address; ``None`` if it is not usable."""
    candidate = (raw or "").strip().strip("<>").lower()
    if not candidate or len(candidate) > MAX_EMAIL_LENGTH:
        return None
    return candidate if _EMAIL_RE.match(candidate) else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _post(
    endpoint: str,
    body: dict,
    *,
    version: str,
    source: str,
    timeout: float,
) -> bool:
    """POST JSON and report a 2xx. Never raises, never retries."""
    if not endpoint:
        return False

    payload = json.dumps(
        {**body, "version": version, "source": source, "ts": int(datetime.now(timezone.utc).timestamp())}
    ).encode("utf-8")

    request = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"astrocoda/{version}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False
    except Exception:  # noqa: BLE001 - telemetry must never break a scaffold
        return False


def submit(
    endpoint: str,
    email: str,
    *,
    version: str = __version__,
    source: str = "cli",
    timeout: float = 3.0,
) -> bool:
    """Record an opt-in. Returns whether the server took it; never raises.

    A timeout, DNS failure, 500 or malformed response is all just a "no".  The
    caller must carry on scaffolding either way, and must not retry, so a user
    never waits on a second attempt.
    """
    return _post(endpoint, {"email": email}, version=version, source=source, timeout=timeout)


def unsubscribe(
    endpoint: str,
    email: str,
    *,
    version: str = __version__,
    source: str = "cli",
    timeout: float = 3.0,
) -> bool:
    """Ask the server to stop emailing this address. Best effort, never raises."""
    return _post(endpoint, {"email": email}, version=version, source=source, timeout=timeout)


def collect(
    *,
    endpoint: str | None = None,
    version: str = __version__,
    source: str = "cli",
    email_flag: str | None = None,
    assume_yes: bool = False,
    assume_no: bool = False,
    interactive: bool | None = None,
    path: Path | None = None,
    input_fn: Callable[[str], str] = input,
    out: TextIO | None = None,
    submitter: Callable[..., bool] = submit,
) -> Identity:
    """Decide whether to ask, ask, submit, and remember the answer.

    Returns the identity to record.  Order of precedence for a supplied
    address: ``--email``, then ``ASTROCODA_EMAIL``.  ``--no-email`` and
    ``ASTROCODA_NO_EMAIL=1`` win over everything and send nothing.
    """
    stream = out if out is not None else sys.stdout
    target = endpoint if endpoint is not None else OPTIN_ENDPOINT
    now = _now()

    if assume_no or os.environ.get("ASTROCODA_NO_EMAIL", "").strip() not in ("", "0", "false", "False"):
        return Identity(declined=True, asked_at=now)

    supplied = email_flag or os.environ.get("ASTROCODA_EMAIL", "").strip()
    if supplied:
        email = normalise_email(supplied)
        if email is None:
            print(f"[i] Ignoring unrecognised email address.", file=stream)
            return Identity(declined=True, asked_at=now)
        if submitter(target, email, version=version, source=source):
            print(f"[ok] Thanks - we'll email {email} about updates.", file=stream)
            return Identity(email=email, asked_at=now)
        print("[i] Could not reach the update list; carrying on.", file=stream)
        return Identity(email=email, asked_at=now)

    existing = load_identity(path)
    if existing is not None and existing.answered:
        return existing

    tty = is_interactive() if interactive is None else interactive
    if not tty:
        return Identity(asked_at=now)

    try:
        answer = (input_fn(PROMPT_TEXT) or "").strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        print(file=stream)
        return Identity(declined=True, asked_at=now)
    except Exception:  # noqa: BLE001 - a courtesy prompt must never break a scaffold
        return Identity(declined=True, asked_at=now)

    if answer in ("y", "yes"):
        try:
            typed = (input_fn("  Email: ") or "").strip()
        except (EOFError, KeyboardInterrupt, OSError):
            typed = ""
        except Exception:  # noqa: BLE001 - as above
            typed = ""
        email = normalise_email(typed)
        if email is None:
            print("[i] No valid address given; skipping.", file=stream)
            return Identity(declined=True, asked_at=now)
        if submitter(target, email, version=version, source=source):
            print(f"[ok] Thanks - we'll email {email} about updates.", file=stream)
        else:
            print("[i] Could not reach the update list; carrying on.", file=stream)
        return Identity(email=email, asked_at=now)

    print("[ok] No problem - scaffolding anyway.", file=stream)
    return Identity(declined=True, asked_at=now)


def forget(path: Path | None = None) -> bool:
    """Delete the local identity record. Returns True when something existed."""
    target = path or identity_file()
    if not target.exists():
        return False
    target.unlink(missing_ok=True)
    return True
