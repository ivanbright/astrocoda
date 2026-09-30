#!/usr/bin/env python
"""Astrocoda first-run bootstrap.

Creates a working ``.env`` in one command, so a fresh clone goes from nothing
to a running stack without hand-editing a dozen variables.

    python bootstrap.py                 # create .env, generate SECRET_KEY, report gaps
    python bootstrap.py --check         # validate .env against the Settings schema
    python bootstrap.py --rotate-secret # mint a new SECRET_KEY (invalidates JWTs)
    python bootstrap.py --force         # overwrite an existing .env

Standard library only - this runs before any dependency is installed.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_TEMPLATE = ROOT / ".env.example"

REQUIRED_VALUES: tuple[str, ...] = (
    "POSTGRES_URI",
    "REDIS_URI",
    "OPENAI_API_KEY",
    "QDRANT_URL",
    "STRIPE_WEBHOOK_SECRET",
    "SECRET_KEY",
)

PLACEHOLDERS: tuple[str, ...] = (
    "sk-replace_me",
    "whsec_replace_me",
    "replace_me",
    "change_me",
)

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def _colour(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{RESET}"


def _ok(message: str) -> None:
    print(f"  {_colour('[ok]', GREEN)}     {message}")


def _warn(message: str) -> None:
    print(f"  {_colour('[!]', YELLOW)}     {message}")


def _fail(message: str) -> None:
    print(f"  {_colour('[x]', RED)}     {message}")


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse a dotenv file into a plain dict, ignoring comments and blanks."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_env(values: dict[str, str], path: Path = ENV_FILE) -> None:
    """Write ``key=value`` pairs back to disk, dropping empty entries."""
    body = "\n".join(f"{key}={value}" for key, value in values.items() if value != "")
    path.write_text(f"{body}\n", encoding="utf-8")


def build_env() -> dict[str, str]:
    """Create ``.env`` from the template with a freshly generated secret."""
    template = ENV_TEMPLATE.read_text(encoding="utf-8")
    values = read_env(ENV_TEMPLATE)

    secret = secrets.token_urlsafe(48)
    template = re.sub(
        r"^SECRET_KEY=.*$",
        f"SECRET_KEY={secret}",
        template,
        flags=re.MULTILINE,
    )

    ENV_FILE.write_text(template, encoding="utf-8")
    _ok(f"Created {ENV_FILE.name} from {ENV_TEMPLATE.name}")
    _ok("Generated a cryptographically random SECRET_KEY")
    return values


def rotate_secret() -> int:
    """Replace SECRET_KEY in place, invalidating every issued access token."""
    values = read_env()
    if not values:
        _fail("No .env file found. Run: python bootstrap.py")
        return 1
    values["SECRET_KEY"] = secrets.token_urlsafe(48)
    write_env(values)
    _ok("Rotated SECRET_KEY. Existing JWT access tokens are now invalid.")
    return 0


def report(values: dict[str, str]) -> int:
    """Print which required values are still placeholders, with next steps."""
    print()
    print(_colour("Environment check", BOLD))
    print(_colour("-" * 52, DIM))

    missing: list[str] = []
    for key in REQUIRED_VALUES:
        value = values.get(key, "")
        if not value:
            _fail(f"{key} is not set")
            missing.append(key)
        elif any(marker in value for marker in PLACEHOLDERS):
            _warn(f"{key} still holds a placeholder value")
            missing.append(key)
        else:
            _ok(f"{key} is configured")

    print()
    if missing:
        print(
            _colour(
                f"  {len(missing)} value(s) need your attention. Edit {ENV_FILE.name} and set:",
                YELLOW,
            )
        )
        for key in missing:
            print(f"    - {key}")
        print()

    print(_colour("Next steps", BOLD))
    print(_colour("-" * 52, DIM))
    print("  1. Fill in the values above in .env")
    print("  2. Start the full stack:        docker compose up --build")
    print("  3. Open the API docs:           http://localhost:8000/docs")
    print("  4. Forward Stripe webhooks:     stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe")
    print("  5. Verify dependencies:         curl http://localhost:8000/health")
    print()
    return 0


def check() -> int:
    """Validate ``.env`` against the real Settings schema when possible."""
    if not ENV_FILE.exists():
        _fail(f"No {ENV_FILE.name} file found. Run: python bootstrap.py")
        return 1

    try:
        from app.core.config import Settings
    except ImportError:
        _warn("Dependencies are not installed yet, skipping schema validation.")
        print("  Install them with: pip install -r requirements.txt")
        return report(read_env())

    try:
        loaded = Settings()
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
        _fail("Configuration is invalid:")
        print(f"    {exc}")
        return 1

    _ok("Configuration validated against app.core.config.Settings")
    for key, value in loaded.redact().items():
        print(f"    {_colour(key.ljust(26), DIM)} {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bootstrap",
        description="Prepare a local Astrocoda environment.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate .env against the Settings schema and print the resolved config.",
    )
    parser.add_argument(
        "--rotate-secret",
        action="store_true",
        help="Generate a new SECRET_KEY, invalidating all issued JWTs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing .env file.",
    )
    args = parser.parse_args(argv)

    print()
    print(_colour("Astrocoda bootstrap", BOLD))
    print(_colour("-" * 52, DIM))

    if args.rotate_secret:
        return rotate_secret()

    if args.check:
        return check()

    if ENV_FILE.exists() and not args.force:
        _ok(f"{ENV_FILE.name} already exists, leaving it untouched")
        _warn("Use --force to regenerate it or --check to validate it")
    else:
        build_env()

    return report(read_env())


if __name__ == "__main__":
    raise SystemExit(main())
