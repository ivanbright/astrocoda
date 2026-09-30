"""``astrocoda`` command line entrypoint.

    astrocoda login <key>     # verify a signed license key locally and store it
    astrocoda status          # show the stored license
    astrocoda logout          # remove the stored license
    astrocoda init <name>     # scaffold a project (requires a valid license)
    astrocoda up [...]        # start the stack (requires a valid license)

Every gated command re-verifies the stored license by its digital signature
before doing work.  Nothing talks to a network, so the seller does not need a
server for the CLI to enforce the license.
"""

from __future__ import annotations

import argparse
from typing import Callable

from astrocoda import __version__
from astrocoda.config import PUBLIC_KEY_PATH
from astrocoda.credentials import (
    Credentials,
    drop_credentials,
    load_credentials,
    save_credentials,
)
from astrocoda.license_key import LicenseKeyError, load_public_key, verify_key
from astrocoda.operations import require_license, run_init, run_up


def _print_error(message: str) -> None:
    print(f"[x] {message}")


def cmd_login(args: argparse.Namespace) -> int:
    """Verify a signed license key locally; store it on success."""
    try:
        public_key = load_public_key(PUBLIC_KEY_PATH.read_bytes())
        claims = verify_key(public_key, args.key)
    except (LicenseKeyError, OSError) as exc:
        _print_error(f"License key rejected: {exc}")
        return 1

    save_credentials(
        Credentials(
            license_key=args.key,
            email=claims.email,
            plan=claims.plan,
            expires_at=claims.expires_at,
        )
    )
    print(f"[ok] Logged in as {claims.email} ({claims.plan}) until {claims.expires_at:%Y-%m-%d}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    credentials = load_credentials()
    print(f"  email      {credentials.email}")
    print(f"  plan       {credentials.plan}")
    print(f"  expires    {credentials.expires_at:%Y-%m-%d %H:%M %Z}")
    return 0


def cmd_logout(_args: argparse.Namespace) -> int:
    removed = drop_credentials()
    print("[ok] Logged out." if removed else "[i] No stored credentials.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    try:
        session = require_license()
    except SystemExit:
        return 1
    print(f"[i] Licensed to {session.email} ({session.plan})")
    try:
        run_init(args.name, source=args.source)
    except SystemExit as exc:
        return int(exc.code or 1)
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    try:
        require_license()
    except SystemExit:
        return 1
    run_up(args.dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astrocoda",
        description="Licensed tooling for the Astrocoda AI pipeline boilerplate (offline license check).",
    )
    parser.add_argument("--version", action="version", version=f"astrocoda {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_login = subparsers.add_parser("login", help="Verify and store a signed license key")
    p_login.add_argument("key", help="License key purchased from the seller")
    p_login.set_defaults(handler=cmd_login)

    subparsers.add_parser("status", help="Show the stored license").set_defaults(handler=cmd_status)
    subparsers.add_parser("logout", help="Remove the stored license").set_defaults(handler=cmd_logout)

    p_init = subparsers.add_parser("init", help="Scaffold a new Astrocoda project")
    p_init.add_argument("name", help="Target project directory")
    p_init.add_argument("--source", default=None, help="Path to the template (defaults to bundled)")
    p_init.set_defaults(handler=cmd_init)

    p_up = subparsers.add_parser("up", help="Start the full stack with docker compose")
    p_up.add_argument("--dir", default=None, help="Project directory (defaults to cwd)")
    p_up.set_defaults(handler=cmd_up)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())