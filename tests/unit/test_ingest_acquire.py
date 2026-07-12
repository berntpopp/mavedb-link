"""Acquisition layer: Zenodo resolve/download, prebuilt bundle, build lock, CLI."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx
import zstandard
from typer.testing import CliRunner

from mavedb_link.data.repository import MirrorRepository
from mavedb_link.exceptions import DataUnavailableError
from mavedb_link.ingest import bundle
from mavedb_link.ingest.builder import build_database
from mavedb_link.ingest.cli import app
from mavedb_link.ingest.downloader import download_file, resolve_latest_dump
from mavedb_link.ingest.lock import build_lock
from tests.dump_fixture import write_mini_dump

runner = CliRunner()


class _ExplodesAfterLimit(httpx.SyncByteStream):
    """Proves callers stop once a streamed metadata limit is exceeded."""

    def __iter__(self):
        yield b"1234"
        yield b"5678"
        raise AssertionError("metadata reader consumed beyond the enforced limit")


def _zenodo_versions() -> dict:
    return {
        "hits": {
            "hits": [
                {
                    "id": 111,
                    "metadata": {"version": "3", "publication_date": "2025-06-12"},
                    "files": [
                        {
                            "key": "v3.zip",
                            "size": 3,
                            "checksum": f"md5:{'a' * 32}",
                            "links": {"self": "https://zenodo.org/files/v3.zip"},
                        }
                    ],
                },
                {
                    "id": 222,
                    "metadata": {"version": "4", "publication_date": "2026-02-06"},
                    "files": [
                        {
                            "key": "v4.zip",
                            "size": 9,
                            "checksum": f"md5:{'b' * 32}",
                            "links": {"self": "https://zenodo.org/files/v4.zip"},
                        }
                    ],
                },
            ]
        }
    }


def test_resolve_latest_picks_max_version() -> None:
    with respx.mock(base_url="https://zenodo.org/api") as mock:
        route = mock.get("/records").mock(return_value=httpx.Response(200, json=_zenodo_versions()))
        ref = resolve_latest_dump("11201736")
    # Zenodo rejects the search with 400 unless a sort is sent (regression guard).
    assert "sort=" in str(route.calls.last.request.url)
    assert ref.version == "4"
    assert ref.record_id == "222"
    assert ref.filename == "v4.zip"
    assert ref.md5 == "b" * 32
    assert ref.url.endswith("v4.zip")


def _zenodo_versionless_newest() -> dict:
    # Real shape as of 2026-06-25: the newest dump (a .tar.gz) carries no
    # metadata.version, while older records still do. "Latest" must follow the
    # publication_date, not the (missing) version number.
    return {
        "hits": {
            "hits": [
                {
                    "id": 18511521,
                    "metadata": {"version": "4", "publication_date": "2026-02-06"},
                    "files": [
                        {
                            "key": "mavedb-dump.20260206153444.zip",
                            "size": 9,
                            "checksum": f"md5:{'c' * 32}",
                            "links": {"self": "https://zenodo.org/files/v4.zip"},
                        }
                    ],
                },
                {
                    "id": 20840937,
                    "metadata": {"version": None, "publication_date": "2026-06-25"},
                    "files": [
                        {
                            "key": "mavedb-dump.2026062418131.tar.gz",
                            "size": 12,
                            "checksum": f"md5:{'d' * 32}",
                            "links": {"self": "https://zenodo.org/files/dump.tar.gz"},
                        }
                    ],
                },
            ]
        }
    }


def test_resolve_latest_prefers_newest_even_without_version_number() -> None:
    with respx.mock(base_url="https://zenodo.org/api") as mock:
        mock.get("/records").mock(
            return_value=httpx.Response(200, json=_zenodo_versionless_newest())
        )
        ref = resolve_latest_dump("11201736")
    # The versionless 2026-06-25 tar.gz is newer than the v4 zip; pick it.
    assert ref.record_id == "20840937"
    assert ref.filename == "mavedb-dump.2026062418131.tar.gz"
    assert ref.url.endswith("dump.tar.gz")


def test_download_verifies_md5(tmp_path: Path) -> None:
    body = b"hello world"
    good = hashlib.md5(body).hexdigest()  # noqa: S324 (integrity, not security)
    expected_sha256 = hashlib.sha256(body).hexdigest()
    with respx.mock() as mock:
        url = "https://zenodo.org/files/file.zip"
        mock.get(url).mock(return_value=httpx.Response(200, content=body))
        dest = tmp_path / "f.zip"
        digest = download_file(url, dest, expected_md5=good)
    assert digest == expected_sha256
    assert dest.read_bytes() == body


def test_download_rejects_bad_md5(tmp_path: Path) -> None:
    destination = tmp_path / "f.zip"
    destination.write_bytes(b"old")
    with respx.mock() as mock:
        url = "https://zenodo.org/files/file.zip"
        mock.get(url).mock(return_value=httpx.Response(200, content=b"hello"))
        with pytest.raises(DataUnavailableError):
            download_file(url, destination, expected_md5="0" * 32)
    assert destination.read_bytes() == b"old"


def test_zenodo_missing_checksum_fails_closed() -> None:
    payload = _zenodo_versions()
    payload["hits"]["hits"][-1]["files"][0]["checksum"] = None
    with respx.mock(base_url="https://zenodo.org/api") as mock:
        mock.get("/records").mock(return_value=httpx.Response(200, json=payload))
        with pytest.raises(DataUnavailableError, match="missing a valid md5 checksum"):
            resolve_latest_dump("11201736")


@respx.mock
def test_zenodo_metadata_limit_stops_streaming() -> None:
    respx.get("https://zenodo.org/api/records").mock(
        return_value=httpx.Response(200, stream=_ExplodesAfterLimit())
    )
    with pytest.raises(DataUnavailableError, match="metadata exceeded 5 bytes"):
        resolve_latest_dump("11201736", max_metadata_bytes=5)


@respx.mock
def test_zenodo_overflow_preserves_existing_dump(tmp_path: Path) -> None:
    destination = tmp_path / "dump.tar.gz"
    destination.write_bytes(b"old")
    url = "https://zenodo.org/records/1/files/dump.tar.gz"
    respx.get(url).mock(return_value=httpx.Response(200, stream=httpx.ByteStream(b"123456789")))
    with pytest.raises(DataUnavailableError, match="exceeded 8 bytes"):
        download_file(
            url,
            destination,
            expected_md5="0" * 32,
            expected_size=9,
            max_bytes=8,
        )
    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob("*.download.tmp")) == []


def test_build_lock_acquires_and_releases(tmp_path: Path) -> None:
    with build_lock(tmp_path / ".build.lock"):
        pass
    assert (tmp_path / ".build.lock").exists()


def test_bundle_pack_and_pull_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "mavedb.sqlite"
    build_database(write_mini_dump(tmp_path), db, zenodo_record="222")
    out, sha = bundle.pack(db, tmp_path / "mavedb.sqlite.zst")
    assert out.exists() and sha.exists()
    sha_digest = sha.read_text().split()[0]
    zst_bytes = out.read_bytes()

    dest = tmp_path / "pulled" / "mavedb.sqlite"
    with respx.mock() as mock:
        mock.get("https://api.github.com/repos/berntpopp/mavedb-link/releases").mock(
            return_value=httpx.Response(
                200,
                json=[
                    # Newest release is a CODE release carrying no assets -- the real shape
                    # of this repo, and what used to make the pull fail closed to live-only.
                    {"tag_name": "v0.4.1", "draft": False, "assets": []},
                    {
                        "tag_name": "data-2026-06-24",
                        "draft": False,
                        "assets": [
                            {
                                "name": "mavedb.sqlite.zst",
                                "browser_download_url": "https://github.com/berntpopp/mavedb-link/releases/download/v1/mavedb.sqlite.zst",
                            }
                        ],
                    },
                ],
            )
        )
        asset_url = (
            "https://github.com/berntpopp/mavedb-link/releases/download/v1/mavedb.sqlite.zst"
        )
        mock.get(asset_url).mock(return_value=httpx.Response(200, content=zst_bytes))
        mock.get(f"{asset_url}.sha256").mock(
            return_value=httpx.Response(200, text=f"{sha_digest}  mavedb.sqlite.zst\n")
        )
        bundle.pull("berntpopp/mavedb-link", "mavedb.sqlite.zst", "latest", dest)

    repo = MirrorRepository.open(dest)
    assert repo is not None
    assert repo.meta()["score_set_count"] == 2
    repo.close()


def test_bundle_pull_rejects_bad_checksum(tmp_path: Path) -> None:
    db = tmp_path / "mavedb.sqlite"
    build_database(write_mini_dump(tmp_path), db, zenodo_record="222")
    out, _ = bundle.pack(db, tmp_path / "mavedb.sqlite.zst")
    dest = tmp_path / "pulled" / "mavedb.sqlite"
    with respx.mock(assert_all_called=False) as mock:
        asset_url = "https://github.com/r/r/releases/download/v1/a.zst"
        asset_route = mock.get(asset_url).mock(
            return_value=httpx.Response(200, content=out.read_bytes())
        )
        mock.get(f"{asset_url}.sha256").mock(
            return_value=httpx.Response(200, text="deadbeef  a.zst\n")
        )
        with pytest.raises(DataUnavailableError):
            bundle.pull("r/r", "a.zst", asset_url, dest)
        assert asset_route.called is False
    assert not dest.exists()


@respx.mock
def test_bundle_missing_checksum_fails_closed(tmp_path: Path) -> None:
    url = "https://github.com/berntpopp/mavedb-link/releases/download/v1/db.zst"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"zstd"))
    respx.get(f"{url}.sha256").mock(return_value=httpx.Response(404))
    with pytest.raises(DataUnavailableError, match="expected SHA-256 is required"):
        bundle.pull("berntpopp/mavedb-link", "db.zst", url, tmp_path / "mavedb.sqlite")


@respx.mock
def test_github_metadata_limit_stops_streaming() -> None:
    respx.get("https://api.github.com/repos/berntpopp/mavedb-link/releases").mock(
        return_value=httpx.Response(200, stream=_ExplodesAfterLimit())
    )
    with pytest.raises(DataUnavailableError, match="metadata exceeded 5 bytes"):
        bundle.resolve_release_asset(
            "berntpopp/mavedb-link",
            "db.zst",
            "latest",
            max_metadata_bytes=5,
        )


def test_bundle_expansion_limit_preserves_database(tmp_path: Path) -> None:
    destination = tmp_path / "mavedb.sqlite"
    destination.write_bytes(b"old")
    compressed = zstandard.ZstdCompressor().compress(b"x" * 65)
    zst_path = tmp_path / "mavedb.sqlite.zst"
    zst_path.write_bytes(compressed)
    with pytest.raises(DataUnavailableError, match="exceeded 64 bytes"):
        bundle._decompress_replace(zst_path, destination, max_expanded_bytes=64)
    assert destination.read_bytes() == b"old"


@respx.mock
def test_bundle_rejects_unapproved_redirect_before_request(tmp_path: Path) -> None:
    url = "https://github.com/berntpopp/mavedb-link/releases/download/v1/db.zst"
    blocked_url = "https://evil.example/db.zst"
    blocked = respx.get(blocked_url).mock(return_value=httpx.Response(200, content=b"bad"))
    respx.get(url).mock(return_value=httpx.Response(302, headers={"Location": blocked_url}))
    with pytest.raises(DataUnavailableError, match=r"host evil\.example is not allowed"):
        bundle.pull(
            "berntpopp/mavedb-link",
            "db.zst",
            url,
            tmp_path / "mavedb.sqlite",
            expected_sha256="a" * 64,
        )
    assert blocked.called is False


# --- CLI -----------------------------------------------------------------------


def test_cli_build_from_local_dump_then_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mavedb_link.config import settings

    monkeypatch.setattr(settings.mirror, "data_dir", tmp_path)
    dump = write_mini_dump(tmp_path)
    built = runner.invoke(app, ["build", "--dump", str(dump)])
    assert built.exit_code == 0, built.output
    assert "score sets" in built.output
    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0
    assert "score_sets=2" in status.output


def test_cli_bootstrap_reuses_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mavedb_link.config import settings

    monkeypatch.setattr(settings.mirror, "data_dir", tmp_path)
    build_database(write_mini_dump(tmp_path), settings.mirror.db_path, zenodo_record="222")
    result = runner.invoke(app, ["bootstrap"])
    assert result.exit_code == 0
    assert "reusing" in result.output


def test_cli_bootstrap_degrades_to_live_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mavedb_link.config import settings

    monkeypatch.setattr(settings.mirror, "data_dir", tmp_path)
    monkeypatch.setattr(settings.mirror, "bundle_url", "")
    monkeypatch.setattr(settings.mirror, "build_local", False)
    result = runner.invoke(app, ["bootstrap"])
    assert result.exit_code == 0
    assert "live-only" in result.output


@respx.mock
def test_resolve_skips_asset_less_code_releases() -> None:
    """'latest' must mean the newest release that CARRIES the asset.

    Regression: the resolver read /releases/latest and scanned only that release's assets.
    The mirror bundle ships in its own dated data-* release, so every code release made
    /releases/latest an asset-less release and the pull failed with "No asset ... in the
    latest release". bootstrap then degraded to live-only while the container still
    reported healthy -- a silent inversion of the mirror-primary posture.
    """
    respx.get("https://api.github.com/repos/berntpopp/mavedb-link/releases").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"tag_name": "v0.4.1", "draft": False, "assets": []},
                {"tag_name": "v0.4.0", "draft": False, "assets": []},
                {
                    "tag_name": "data-2026-06-24",
                    "draft": False,
                    "assets": [
                        {
                            "name": "mavedb.sqlite.zst",
                            "browser_download_url": "https://github.com/berntpopp/mavedb-link/releases/download/data-2026-06-24/mavedb.sqlite.zst",
                            "digest": "sha256:" + "a" * 64,
                            "size": 1234,
                        }
                    ],
                },
            ],
        )
    )
    asset = bundle.resolve_release_asset("berntpopp/mavedb-link", "mavedb.sqlite.zst", "latest")
    assert asset.url.endswith("/data-2026-06-24/mavedb.sqlite.zst")
    assert asset.sha256 == "a" * 64
    assert asset.size == 1234


@respx.mock
def test_resolve_fails_closed_when_no_release_carries_the_asset() -> None:
    """A genuinely absent bundle must still fail closed, not pick something else."""
    respx.get("https://api.github.com/repos/berntpopp/mavedb-link/releases").mock(
        return_value=httpx.Response(
            200,
            json=[{"tag_name": "v1", "draft": False, "assets": [{"name": "other.zst"}]}],
        )
    )
    with pytest.raises(DataUnavailableError, match="publishes an asset"):
        bundle.resolve_release_asset("berntpopp/mavedb-link", "mavedb.sqlite.zst", "latest")


@respx.mock
def test_resolve_skips_draft_releases() -> None:
    """A half-cut draft must never be selected over a published bundle."""
    respx.get("https://api.github.com/repos/berntpopp/mavedb-link/releases").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tag_name": "draft",
                    "draft": True,
                    "assets": [
                        {
                            "name": "mavedb.sqlite.zst",
                            "browser_download_url": "https://github.com/x/y/releases/download/draft/mavedb.sqlite.zst",
                        }
                    ],
                },
                {
                    "tag_name": "data-2026-06-24",
                    "draft": False,
                    "assets": [
                        {
                            "name": "mavedb.sqlite.zst",
                            "browser_download_url": "https://github.com/x/y/releases/download/data-2026-06-24/mavedb.sqlite.zst",
                        }
                    ],
                },
            ],
        )
    )
    asset = bundle.resolve_release_asset("berntpopp/mavedb-link", "mavedb.sqlite.zst", "latest")
    assert "/draft/" not in asset.url
    assert asset.url.endswith("/data-2026-06-24/mavedb.sqlite.zst")
