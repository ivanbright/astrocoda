"""High level operations performed by ``astrocoda`` commands.

Every gated command verifies the stored license *locally* via its digital
signature before doing any work.  ``init`` additionally verifies the template
against the seller's signed manifest (supply-chain check) before copying a file.
There is no network and no server.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from astrocoda.config import DEFAULT_TEMPLATE, PUBLIC_KEY_PATH
from astrocoda.license_key import LicenseKeyError, load_public_key, verify_key
from astrocoda.manifest import TemplateIntegrityError, copy_verified, verify_template


@dataclass
class SessionInfo:
    """Decoded identity of the authenticated buyer (already signature-verified)."""

    email: str
    plan: str
    expires_at: str

    @property
    def expires_at_as_datetime(self):
        from datetime import datetime

        return datetime.fromisoformat(self.expires_at)


def _load_license_key() -> str:
    """Fetch the stored raw license key, or raise SystemExit(1)."""
    from astrocoda.credentials import load_credentials

    try:
        return load_credentials().license_key
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim as the gate's reason
        print(f"[x] {exc}")
        raise SystemExit(1) from exc


def require_license(key_factory=_load_license_key) -> SessionInfo:
    """Re-verify the stored license key signature locally; fail closed."""
    key = key_factory()
    try:
        public_key = load_public_key(PUBLIC_KEY_PATH.read_bytes())
        claims = verify_key(public_key, key)
    except (LicenseKeyError, OSError) as exc:
        print(f"[x] License check failed: {exc}")
        print("    Run: astrocoda login <key> with a valid license key")
        raise SystemExit(1) from exc

    return SessionInfo(
        email=claims.email,
        plan=claims.plan,
        expires_at=claims.expires_at.isoformat(),
    )


def _copy_tree(source: Path, target: Path) -> None:
    """Copy the boilerplate, dropping VCS/env/build clutter."""
    ignored = shutil.ignore_patterns(*EXCLUDE)
    shutil.copytree(source, target, ignore=ignored)


def run_init(target: str, source: str | None = None) -> None:
    """Scaffold a fresh Astrocoda project from a signed template."""
    source_path = Path(source).resolve() if source else DEFAULT_TEMPLATE
    target_path = Path(target).resolve()

    if not source_path.exists():
        print(f"[x] Template not found at {source_path}")
        raise SystemExit(1)

    if target_path.exists():
        print(f"[x] {target_path} already exists. Choose another name or remove it.")
        raise SystemExit(1)

    print(f"[i] Scaffolding Astrocoda from {source_path}")

    # Supply-chain gate: the tree must match the seller's signed manifest.
    try:
        public_key = load_public_key(PUBLIC_KEY_PATH.read_bytes())
        files = verify_template(public_key, source_path)
    except (LicenseKeyError, TemplateIntegrityError, OSError) as exc:
        print(f"[x] Template integrity check failed: {exc}")
        print("    Refusing to scaffold. Use a template that ships a valid")
        print("    astrocoda.manifest.json signed by the seller.")
        raise SystemExit(1) from exc

    print(f"[ok] Template verified against seller signature ({len(files)} files signed)")
    copy_verified(source_path, target_path, files)
    print(f"[ok] Created {target_path}")

    bootstrap = target_path / "bootstrap.py"
    if bootstrap.exists():
        print("[i] Generating .env with a fresh SECRET_KEY ...")
        subprocess.run([sys.executable, str(bootstrap)], cwd=target_path, check=True)
    else:
        print("[!] Template did not ship a bootstrap.py; create .env manually.")

    print()
    print("  Next:")
    print(f"    cd {target}")
    print("    astrocoda up          # start postgres, redis, qdrant, api, worker")
    print("    make test             # run the hermetic suite")


def run_up(project_dir: str | None = None) -> None:
    """Start the full stack with ``docker compose`` in the target directory."""
    directory = Path(project_dir).resolve() if project_dir else Path.cwd()
    compose = directory / "docker-compose.yml"
    if not compose.exists():
        print(f"[x] No docker-compose.yml in {directory}")
        print("    Run: astrocoda init <name>")
        raise SystemExit(1)

    print(f"[i] Starting stack in {directory} (this may take a while on first build) ...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "--build", "-d"],
            cwd=directory,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print("[x] `docker compose up` failed (see output above).")
        print("    Is Docker Desktop running? Try `docker compose config` in your project.")
        raise SystemExit(exc.returncode) from exc
    except FileNotFoundError as exc:
        print("[x] `docker` was not found on PATH. Install Docker Desktop and retry.")
        raise SystemExit(1) from exc
    web_port = "8000"
    env_file = directory / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("WEB_PORT="):
                web_port = line.split("=", 1)[1].strip() or web_port
                break
    print()
    print(f"  API    http://localhost:{web_port}/docs")
    print(f"  Health http://localhost:{web_port}/health")
    print("  Worker logs: docker compose logs -f worker")