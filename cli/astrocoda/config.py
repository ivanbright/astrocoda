"""Central configuration for the CLI: paths and the embedded public key."""

from __future__ import annotations

import os
from pathlib import Path

#: Name of the signed manifest that must travel with any template. Spelled out
#: here rather than imported so that this module stays dependency-free.
MANIFEST_NAME: str = "astrocoda.manifest.json"

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

#: Template used by ``astrocoda init`` when no ``--source`` is given and no
#: download is possible.
#:
#: Historically this was unconditionally ``parents[2]``, which resolves to
#: ``site-packages`` for an installed CLI.  That directory exists but contains
#: no manifest, so ``init`` failed for every real user while appearing to work
#: in a repository checkout.  A template is only accepted here if it actually
#: carries a manifest; otherwise ``init`` fetches a signed release instead.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def _discover_bundled_template() -> Path | None:
    """Return a usable adjacent template, or ``None`` if there is not one."""
    override = os.environ.get("ASTROCODA_TEMPLATE")
    candidates = [Path(override)] if override else []
    candidates.append(REPO_ROOT)
    for candidate in candidates:
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    return None


DEFAULT_TEMPLATE: Path | None = _discover_bundled_template()

#: Where signed releases are published. The default matches the GitHub Release
#: asset layout for this repository; point ASTROCODA_RELEASE_BASE_URL at a mirror
#: or an internal host if you distribute the CLI yourself.
#:
#: Only HTTPS (or an explicit file:// for local testing) is accepted. A release is
#: code that will be copied onto a user's disk, so transport is part of the trust
#: story -- and the signature is checked afterwards regardless, so a hostile
#: mirror can only ever cause a clean failure.
RELEASE_BASE_URL: str = os.environ.get(
    "ASTROCODA_RELEASE_BASE_URL",
    "https://github.com/ivanbright/astrocoda/releases/download/v{version}/astrocoda-{version}.zip",
)

#: Downloaded releases are cached here and reused, so scaffolding a second
#: project does not hit the network again.
TEMPLATE_CACHE_DIR: Path = CREDENTIALS_DIR / "templates"

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
        # Packaging artifacts. ``pip install ./cli`` writes these into the source
        # tree, and a stale ``cli/build/lib/astrocoda/`` copy of the CLI used to
        # ship inside the release archive, complete with outdated source.
        "build",
        "dist",
        "*.egg-info",
        ".mypy_cache",
        ".ruff_cache",
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