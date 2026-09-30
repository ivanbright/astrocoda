"""Download a signed Astrocoda release so ``init`` needs no arguments.

This is the only module in the CLI that touches the network, and it never trusts
what it downloads.  The archive is unpacked into a cache directory, then the
seller's manifest is verified against the public key embedded in the package
*before* any file reaches the user's project.  A failed verification destroys the
cache entry, so a tampered download can never be reused by a later ``init``.

The transport is treated as untrusted on purpose: the signature is what
establishes trust, not the URL.  A hostile mirror can therefore only ever cause
a clean, explained failure.
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from astrocoda.config import RELEASE_BASE_URL, TEMPLATE_CACHE_DIR
from astrocoda.manifest import MANIFEST_NAME, TemplateIntegrityError, verify_manifest_signature

#: Refuse absurd downloads rather than filling a user's disk.
MAX_RELEASE_BYTES: int = 64 * 1024 * 1024

#: Cap on uncompressed expansion, so a small archive cannot zip-bomb the cache.
MAX_UNCOMPRESSED_BYTES: int = 256 * 1024 * 1024

_USER_AGENT = "astrocoda-cli"


class ReleaseFetchError(Exception):
    """A release could not be downloaded, or was rejected."""


def release_url(version: str, base_url: str | None = None) -> str:
    """Return the download URL for ``version``.

    A ``{version}`` placeholder is substituted when present; otherwise the value
    is used verbatim, so a single fixed artifact URL also works.
    """
    template = (base_url or RELEASE_BASE_URL).strip()
    if not template:
        raise ReleaseFetchError("ASTROCODA_RELEASE_BASE_URL is empty.")
    if "{version}" not in template:
        return template
    try:
        return template.format(version=version)
    except (KeyError, IndexError) as exc:  # pragma: no cover - defensive
        raise ReleaseFetchError("Malformed release URL template.") from exc


def _check_scheme(url: str) -> None:
    """Allow HTTPS, plus file:// so the flow can be tested against a local zip."""
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ("https", "file"):
        raise ReleaseFetchError(
            f"Refusing to download over '{scheme or 'no'}'. Use https:// or file://."
        )


def _download(url: str, destination: Path) -> None:
    """Stream ``url`` to ``destination``, enforcing the size cap."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > MAX_RELEASE_BYTES:
                raise ReleaseFetchError("Release is implausibly large; refusing to download.")
            written = 0
            with destination.open("wb") as handle:
                while True:
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_RELEASE_BYTES:
                        raise ReleaseFetchError("Release exceeded the size cap mid-download.")
                    handle.write(block)
    except ReleaseFetchError:
        raise
    except urllib.error.HTTPError as exc:
        raise ReleaseFetchError(
            f"Release download failed ({exc.code} {exc.reason}). "
            "Check the version, or pass --source to scaffold from a local copy."
        ) from exc
    except urllib.error.URLError as exc:
        raise ReleaseFetchError(f"Could not reach the release host: {exc.reason}") from exc
    if written == 0:
        raise ReleaseFetchError("Release download was empty.")


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    """Extract ``archive`` into ``destination``, refusing entries that escape it.

    ``ZipFile.extractall`` happily writes ``../../.ssh/authorized_keys``.  We are
    unpacking untrusted bytes, so every member is checked first and the whole
    destination is discarded on any violation.
    """
    root = destination.resolve()
    total = 0
    for member in archive.infolist():
        total += member.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ReleaseFetchError("Release expands to an implausible size; refusing to unpack.")
        target = (root / member.filename).resolve()
        if not target.is_relative_to(root):
            raise ReleaseFetchError(
                f"Release archive contains an unsafe path: {member.filename!r}"
            )
    archive.extractall(root)


def _resolve_root(staging: Path) -> Path:
    """Return the directory inside ``staging`` that holds the manifest.

    Release archives are usually built with a single versioned top-level
    directory (``astrocoda-0.1.0/...``), but a flat archive is equally valid.
    Both are accepted; an archive that nests the manifest more deeply is
    rejected rather than guessed at, because that shape has no single obvious
    root and guessing is how a path-traversal bug gets in.
    """
    if (staging / MANIFEST_NAME).is_file():
        return staging
    directories = [entry for entry in staging.iterdir() if entry.is_dir() and not entry.name.startswith(".")]
    if len(directories) == 1 and (directories[0] / MANIFEST_NAME).is_file():
        return directories[0]
    raise ReleaseFetchError(
        f"Downloaded release has no {MANIFEST_NAME} at its root or under a single "
        "top-level directory; refusing to use it."
    )


def fetch_release(
    version: str,
    public_key,
    *,
    offline: bool = False,
    base_url: str | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Return a verified template directory for ``version``.

    The signature is checked before returning, so the caller can hand the path
    straight to :func:`~astrocoda.manifest.verify_template` without trusting the
    download at all.
    """
    cache_root = cache_dir or TEMPLATE_CACHE_DIR
    target = cache_root / version
    manifest_path = target / MANIFEST_NAME

    if manifest_path.is_file():
        try:
            verify_manifest_signature(public_key, _load_manifest(manifest_path))
        except TemplateIntegrityError:
            # Corrupt or tampered cache entry: drop it rather than reuse it.
            shutil.rmtree(target, ignore_errors=True)
        else:
            return target

    if offline:
        raise ReleaseFetchError(
            f"No verified release for {version} in the cache and --offline was requested. "
            "Run 'astrocoda init <name> --source <path>' to scaffold from a local copy."
        )

    url = release_url(version, base_url)
    _check_scheme(url)

    cache_root.mkdir(parents=True, exist_ok=True)
    partial = cache_root / f"astrocoda-{version}.zip.part"
    final = cache_root / f"astrocoda-{version}.zip"
    staging = cache_root / f".staging-{version}"

    try:
        _download(url, partial)
        partial.replace(final)

        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(final) as archive:
            _safe_extract(archive, staging)

        root = _resolve_root(staging)
        verify_manifest_signature(public_key, _load_manifest(root / MANIFEST_NAME))

        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(root), str(target))
    except (ReleaseFetchError, TemplateIntegrityError, zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        partial.unlink(missing_ok=True)
        if isinstance(exc, ReleaseFetchError):
            raise
        if isinstance(exc, TemplateIntegrityError):
            raise ReleaseFetchError(
                f"Downloaded release is not correctly signed: {exc}"
            ) from exc
        raise ReleaseFetchError(f"Could not unpack the release: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return target


def _load_manifest(path: Path) -> dict:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateIntegrityError(f"Template manifest is unreadable: {exc}") from exc
