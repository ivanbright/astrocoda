"""Hermetic tests for release fetching: the one part of the CLI that uses the network.

Nothing here touches a real host.  Releases are built as zip files in ``tmp_path``
and served over ``file://``, which is why :func:`astrocoda.fetch._check_scheme`
allows that scheme; every other test asserts the *failure* modes, which matter
more than the happy path, because a scaffolder that copies attacker-chosen bytes
onto a user's disk is only as trustworthy as its rejection logic.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from astrocoda.fetch import ReleaseFetchError, fetch_release, release_url
from astrocoda.manifest import (
    MANIFEST_NAME,
    TemplateIntegrityError,
    build_manifest,
    sign_manifest,
    verify_template,
)


@pytest.fixture
def private_key() -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.generate()


@pytest.fixture
def public_key(private_key) -> ed25519.Ed25519PublicKey:
    return private_key.public_key()


def build_release(
    private_key,
    destination: Path,
    files: dict[str, bytes] | None = None,
    *,
    prefix: str | None = "astrocoda-0.1.0",
    mutate=None,
) -> Path:
    """Write a signed release archive and return its path.

    The tree is hashed by the real :func:`build_manifest` rather than a
    hand-rolled stand-in, so the tests exercise the production canonicalisation
    and the same exclusion rules the release builder uses.

    ``prefix`` reproduces the versioned top-level directory that the real
    release builder emits; pass ``None`` for a flat archive.
    """
    files = files or {"README.md": b"# hello\n", "app/main.py": b"print('x')\n"}
    tree = destination.parent / "tree"
    for name, data in files.items():
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    manifest = build_manifest(tree, exclude=frozenset(), version="0.1.0")
    signed = sign_manifest(manifest, private_key)
    if mutate is not None:
        signed = mutate(signed)

    def arcname(name: str) -> str:
        return f"{prefix}/{name}" if prefix else name

    with zipfile.ZipFile(destination, "w") as archive:
        for name, data in files.items():
            archive.writestr(arcname(name), data)
        archive.writestr(arcname(MANIFEST_NAME), json.dumps(signed, indent=2).encode())
    return destination


def fetch(public_key, archive: Path, cache: Path, version: str = "0.1.0") -> Path:
    return fetch_release(version, public_key, base_url=archive.as_uri(), cache_dir=cache)


class TestReleaseUrl:
    def test_substitutes_version_placeholder(self):
        url = release_url("0.2.0", "https://host/dl/astrocoda-{version}.zip")
        assert url == "https://host/dl/astrocoda-0.2.0.zip"

    def test_fixed_url_without_placeholder_is_used_verbatim(self):
        assert release_url("0.2.0", "https://host/dl/latest.zip") == "https://host/dl/latest.zip"

    def test_empty_url_is_rejected(self):
        with pytest.raises(ReleaseFetchError):
            release_url("0.2.0", "   ")


class TestTransport:
    def test_non_http_scheme_is_refused(self, public_key, tmp_path):
        with pytest.raises(ReleaseFetchError, match="Refusing to download"):
            fetch_release("0.1.0", public_key, base_url="http://host/a.zip", cache_dir=tmp_path)

    def test_garbage_scheme_is_refused(self, public_key, tmp_path):
        with pytest.raises(ReleaseFetchError):
            fetch_release("0.1.0", public_key, base_url="ftp://host/a.zip", cache_dir=tmp_path)

    def test_missing_host_reports_cleanly(self, public_key, tmp_path):
        with pytest.raises(ReleaseFetchError, match="Could not reach"):
            fetch_release(
                "0.1.0",
                public_key,
                base_url="https://no-such-host.invalid/a.zip",
                cache_dir=tmp_path,
            )


class TestHappyPath:
    def test_fetches_and_verifies_versioned_archive(self, private_key, public_key, tmp_path):
        archive = build_release(private_key, tmp_path / "rel.zip")
        root = fetch(public_key, archive, tmp_path / "cache")
        assert (root / "README.md").read_bytes() == b"# hello\n"
        assert verify_template(public_key, root)

    def test_accepts_flat_archive_without_top_level_directory(self, private_key, public_key, tmp_path):
        archive = build_release(private_key, tmp_path / "flat.zip", prefix=None)
        root = fetch(public_key, archive, tmp_path / "cache")
        assert (root / "app" / "main.py").exists()

    def test_falls_back_to_source_path_shape(self, private_key, public_key, tmp_path):
        """The cache entry is the template root, with no wrapper directory."""
        archive = build_release(private_key, tmp_path / "rel.zip")
        root = fetch(public_key, archive, tmp_path / "cache")
        assert root == tmp_path / "cache" / "0.1.0"
        assert not (root / "astrocoda-0.1.0").exists()


class TestCaching:
    def test_second_fetch_does_not_touch_the_network(self, private_key, public_key, tmp_path):
        archive = build_release(private_key, tmp_path / "rel.zip")
        fetch(public_key, archive, tmp_path / "cache")
        # Point at a dead host: a cache hit must not need the network at all.
        again = fetch_release(
            "0.1.0", public_key, base_url="https://no-such-host.invalid/x.zip", cache_dir=tmp_path / "cache"
        )
        assert again == tmp_path / "cache" / "0.1.0"

    def test_offline_with_empty_cache_fails_and_creates_nothing(self, public_key, tmp_path):
        with pytest.raises(ReleaseFetchError, match="--offline"):
            fetch_release("0.1.0", public_key, offline=True, cache_dir=tmp_path / "cache")
        assert not (tmp_path / "cache" / "0.1.0").exists()

    def test_tampered_cache_entry_is_discarded_not_reused(self, private_key, public_key, tmp_path):
        archive = build_release(private_key, tmp_path / "rel.zip")
        root = fetch(public_key, archive, tmp_path / "cache")
        manifest = json.loads((root / MANIFEST_NAME).read_text())
        manifest["version"] = "6.6.6"
        (root / MANIFEST_NAME).write_text(json.dumps(manifest))
        with pytest.raises(ReleaseFetchError):
            fetch_release(
                "0.1.0",
                public_key,
                base_url="https://no-such-host.invalid/x.zip",
                cache_dir=tmp_path / "cache",
            )
        assert not root.exists()


class TestRejection:
    def test_tampered_schema_is_rejected(self, private_key, public_key, tmp_path):
        """The signature covers every manifest field, not just ``files``."""
        archive = build_release(
            private_key,
            tmp_path / "schema.zip",
            mutate=lambda m: {**m, "schema": 2},
        )
        with pytest.raises(ReleaseFetchError, match="not correctly signed"):
            fetch(public_key, archive, tmp_path / "cache")

    def test_tampered_version_is_rejected(self, private_key, public_key, tmp_path):
        archive = build_release(
            private_key,
            tmp_path / "version.zip",
            mutate=lambda m: {**m, "version": "9.9.9"},
        )
        with pytest.raises(ReleaseFetchError):
            fetch(public_key, archive, tmp_path / "cache")

    def test_forged_signature_is_rejected(self, private_key, public_key, tmp_path):
        archive = build_release(
            private_key,
            tmp_path / "forged.zip",
            mutate=lambda m: {**m, "signature": "aGVsbG8td29ybGQ_dGVzdC1zaWduYXR1cmU"},
        )
        with pytest.raises(ReleaseFetchError):
            fetch(public_key, archive, tmp_path / "cache")

    def test_wrong_signing_key_is_rejected(self, public_key, tmp_path):
        """A release signed by someone else's key must not scaffold."""
        other = ed25519.Ed25519PrivateKey.generate()
        archive = build_release(other, tmp_path / "other.zip")
        with pytest.raises(ReleaseFetchError):
            fetch(public_key, archive, tmp_path / "cache")

    def test_archive_without_manifest_is_refused(self, public_key, tmp_path):
        archive = tmp_path / "bare.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("astrocoda-0.1.0/README.md", b"# hello\n")
        with pytest.raises(ReleaseFetchError, match="no astrocoda.manifest.json"):
            fetch(public_key, archive, tmp_path / "cache")

    def test_manifest_nested_too_deeply_is_refused(self, private_key, public_key, tmp_path):
        archive = tmp_path / "deep.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("a/b/astrocoda.manifest.json", b"{}")
        with pytest.raises(ReleaseFetchError, match="no astrocoda.manifest.json"):
            fetch(public_key, archive, tmp_path / "cache")

    def test_not_a_zip_is_refused(self, public_key, tmp_path):
        archive = tmp_path / "junk.zip"
        archive.write_bytes(b"this is not a zip file")
        with pytest.raises(ReleaseFetchError):
            fetch(public_key, archive, tmp_path / "cache")


class TestZipSlip:
    def test_escaping_member_is_refused(self, public_key, tmp_path):
        """``ZipFile.extractall`` writes ``../`` happily; we must not."""
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("../../../../pwned.txt", "owned")
        with pytest.raises(ReleaseFetchError, match="unsafe path"):
            fetch(public_key, archive, tmp_path / "cache")
        assert not (tmp_path / "pwned.txt").exists()
        assert not (tmp_path.parent / "pwned.txt").exists()

    def test_absolute_member_is_refused(self, public_key, tmp_path):
        """An absolute member is refused, on whichever platform is running.

        The member name must be absolute *for this platform*: ``C:/...`` is a
        perfectly legal relative filename on POSIX (the colon is just a
        character), so a Windows-shaped path here would assert nothing on Linux.
        """
        member = "C:/Windows/Temp/pwned.txt" if os.name == "nt" else "/tmp/pwned.txt"
        archive = tmp_path / "abs.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr(member, "owned")
        with pytest.raises(ReleaseFetchError, match="unsafe path"):
            fetch(public_key, archive, tmp_path / "cache")
        assert not Path(member).exists()


class TestLayeredIntegrity:
    def test_tampered_file_passes_fetch_but_fails_verify_template(
        self, private_key, public_key, tmp_path
    ):
        """Documents the two-layer design.

        ``fetch_release`` authenticates the manifest.  A file edited *after*
        signing leaves that signature intact, so the hash check in
        ``verify_template`` is what must catch it -- and it does, before
        ``run_init`` copies anything.
        """
        files = {"README.md": b"# hello\n", "app/main.py": b"print('x')\n"}
        archive = build_release(private_key, tmp_path / "rel.zip", files)
        root = fetch(public_key, archive, tmp_path / "cache")
        (root / "README.md").write_bytes(b"# hello\n# injected\n")
        with pytest.raises(TemplateIntegrityError, match="does not match"):
            verify_template(public_key, root)

    def test_missing_signed_file_is_caught(self, private_key, public_key, tmp_path):
        archive = build_release(private_key, tmp_path / "rel.zip")
        root = fetch(public_key, archive, tmp_path / "cache")
        (root / "app" / "main.py").unlink()
        with pytest.raises(TemplateIntegrityError, match="missing a signed file"):
            verify_template(public_key, root)
