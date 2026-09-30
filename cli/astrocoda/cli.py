"""``astrocoda`` command line entrypoint.

    astrocoda init <name>     # scaffold a project (no account, no key)
    astrocoda identify [mail] # optionally share an email for update news
    astrocoda status          # show what this machine has shared
    astrocoda optout          # forget the shared email and unsubscribe
    astrocoda up [...]        # start the stack

Nothing is gated.  The boilerplate is MIT licensed, so ``init`` downloads and
verifies the same template whether or not an address is ever supplied.  The only
network call beyond the release download is the optional opt-in, which is best
effort: a declined prompt, a malformed address or an unreachable server all leave
the scaffold untouched.
"""

from __future__ import annotations

import argparse
from typing import Callable

from astrocoda import __version__
from astrocoda.config import DEFAULT_TEMPLATE_VERSION, OPTIN_ENDPOINT, OPTIN_UNSUBSCRIBE_ENDPOINT
from astrocoda.identify import (
    Identity,
    collect,
    forget,
    load_identity,
    normalise_email,
    save_identity,
    submit,
    unsubscribe,
)
from astrocoda.operations import run_init, run_up


def _print_error(message: str) -> None:
    print(f"[x] {message}")


def cmd_identify(args: argparse.Namespace) -> int:
    """Share an email address for update news. Entirely optional."""
    if not args.email:
        print("Nothing shared. Run: astrocoda identify you@example.com")
        return 0

    email = normalise_email(args.email)
    if email is None:
        _print_error("That does not look like an email address.")
        return 1

    if submit(OPTIN_ENDPOINT, email, source="cli-identify"):
        print(f"[ok] Thanks - we'll email {email} about updates.")
    else:
        print("[i] Could not reach the update list; try again later.")

    save_identity(Identity(email=email))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    print(f"  astrocoda  {__version__}")

    identity = load_identity()
    if identity is None:
        print("  email      not shared")
        return 0
    if identity.email:
        print(f"  email      {identity.email}")
    else:
        print("  email      declined - nothing shared")
    if identity.asked_at:
        print(f"  asked      {identity.asked_at}")
    return 0


def cmd_optout(args: argparse.Namespace) -> int:
    identity = load_identity()
    removed = forget()

    if identity is None or not identity.email:
        print("[ok] Forgot the stored preferences." if removed else "[i] Nothing was shared.")
        return 0

    if args.local_only:
        print("[ok] Forgot the stored email; the server copy was left alone.")
    elif unsubscribe(OPTIN_UNSUBSCRIBE_ENDPOINT, identity.email, source="cli-optout"):
        print("[ok] Unsubscribed and forgot the stored email.")
    else:
        print("[i] Forgot the stored email, but could not reach the server to unsubscribe.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    collect(
        email_flag=args.email,
        assume_no=args.no_email,
    )

    try:
        run_init(
            args.name,
            source=args.source,
            version=args.version,
            offline=args.offline,
            release_base_url=args.release_base_url,
        )
    except SystemExit as exc:
        return int(exc.code or 1)
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    try:
        run_up(args.dir)
    except SystemExit as exc:
        return int(exc.code or 1)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astrocoda",
        description="Scaffold the Astrocoda AI pipeline boilerplate. No account required.",
    )
    parser.add_argument("--version", action="version", version=f"astrocoda {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Scaffold a new Astrocoda project")
    p_init.add_argument("name", help="Target project directory")
    p_init.add_argument(
        "--source",
        default=None,
        help="Scaffold from a local template instead of downloading one",
    )
    p_init.add_argument(
        "--version",
        dest="version",
        default=None,
        help=f"Template release to install (default: {DEFAULT_TEMPLATE_VERSION})",
    )
    p_init.add_argument(
        "--offline",
        action="store_true",
        help="Never download; use a release already in the local cache",
    )
    p_init.add_argument(
        "--release-url",
        dest="release_base_url",
        default=None,
        help="Override the release base URL (must contain '{version}')",
    )
    p_init.add_argument(
        "--email",
        default=None,
        help="Share this email for update news instead of being asked (optional)",
    )
    p_init.add_argument(
        "--no-email",
        action="store_true",
        help="Never ask about email and share nothing",
    )
    p_init.set_defaults(handler=cmd_init)

    p_identify = subparsers.add_parser("identify", help="Share an email for update news (optional)")
    p_identify.add_argument("email", nargs="?", default=None, help="Address to share")
    p_identify.set_defaults(handler=cmd_identify)

    subparsers.add_parser("status", help="Show what this machine has shared").set_defaults(
        handler=cmd_status
    )

    p_optout = subparsers.add_parser("optout", help="Forget the shared email and unsubscribe")
    p_optout.add_argument(
        "--local-only",
        dest="local_only",
        action="store_true",
        help="Only delete the local record; do not contact the server",
    )
    p_optout.set_defaults(handler=cmd_optout)

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
