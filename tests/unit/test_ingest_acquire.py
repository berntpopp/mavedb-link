"""Acquisition layer: Zenodo resolve/download, prebuilt bundle, build lock, CLI."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx
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
                            "checksum": "md5:aaa",
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
                            "checksum": "md5:bbb",
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
    assert ref.md5 == "bbb"
    assert ref.url.endswith("v4.zip")


def test_download_verifies_md5(tmp_path: Path) -> None:
    body = b"hello world"
    good = hashlib.md5(body).hexdigest()  # noqa: S324 (integrity, not security)
    with respx.mock() as mock:
        mock.get("https://x/file.zip").mock(return_value=httpx.Response(200, content=body))
        dest = tmp_path / "f.zip"
        digest = download_file("https://x/file.zip", dest, expected_md5=good)
    assert digest == good
    assert dest.read_bytes() == body


def test_download_rejects_bad_md5(tmp_path: Path) -> None:
    with respx.mock() as mock:
        mock.get("https://x/file.zip").mock(return_value=httpx.Response(200, content=b"hello"))
        with pytest.raises(DataUnavailableError):
            download_file("https://x/file.zip", tmp_path / "f.zip", expected_md5="deadbeef")
    assert not (tmp_path / "f.zip").exists()


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
        mock.get("https://api.github.com/repos/berntpopp/mavedb-link/releases/latest").mock(
            return_value=httpx.Response(
                200,
                json={
                    "assets": [
                        {
                            "name": "mavedb.sqlite.zst",
                            "browser_download_url": "https://dl/mavedb.sqlite.zst",
                        }
                    ]
                },
            )
        )
        mock.get("https://dl/mavedb.sqlite.zst").mock(
            return_value=httpx.Response(200, content=zst_bytes)
        )
        mock.get("https://dl/mavedb.sqlite.zst.sha256").mock(
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
    with respx.mock() as mock:
        mock.get("https://dl/a.zst").mock(
            return_value=httpx.Response(200, content=out.read_bytes())
        )
        mock.get("https://dl/a.zst.sha256").mock(
            return_value=httpx.Response(200, text="deadbeef  a.zst\n")
        )
        with pytest.raises(DataUnavailableError):
            bundle.pull("r/r", "a.zst", "https://dl/a.zst", dest)
    assert not dest.exists()


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
