"""Central configuration for the CLI: paths and the embedded public key."""

from __future__ import annotations

import os
from pathlib import Path

#: Credentials live in the user's home directory.
CREDENTIALS_DIR: Path = Path(
    os.environ.get("ASTROCODA_HOME", str(Path.home() / ".astrocoda"))
)
CREDENTIALS_FILE: Path = CREDENTIALS_DIR / "credentials.json"

#: The seller's Ed25519 public key, embedded at build time.  This is the only
#: cryptographic material that ships to buyers; the private key never leaves
#: the seller's machine.
PUBLIC_KEY_PATH: Path = Path(
    os.environ.get("ASTROCODA_PUBLIC_KEY", str(Path(__file__).resolve().parent / "license_public.pem"))
)

#: Default template used by ``astrocoda init`` when none is supplied.  The CLI
#: discovers the boilerplate it shipped with, but ``--template`` can point at
#: any copy (e.g. a private repository clone).
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE: Path = Path(
    os.environ.get("ASTROCODA_TEMPLATE", str(PROJECT_ROOT))
)

#: Files and directories never copied into an initialised project.
#:
#: ``scripts``, ``keys`` and ``seller`` are belt-and-braces: the seller tooling
#: (keygen, release builder, payment store) lives in the separate
#: ``astrocoda-seller`` workspace and must never reach a buyer, so if any of it
#: is ever copied into the product tree it is still refused.
EXCLUDE: frozenset[str] = frozenset(
    {
        ".git",
        ".gitignore",
        ".gitattributes",
        ".venv",
        ".env",
        "__pycache__",
        ".pytest_cache",
        "cli",
        "keys",
        "tests",
        "scripts",
        "seller",
        "store",
        # Repository tooling, not product: CI definitions have no business in a
        # buyer's release archive. LICENSE, SECURITY.md and CONTRIBUTING.md are
        # deliberately kept — shipping MIT-licensed code without its licence
        # would be a problem.
        ".github",
    }
)