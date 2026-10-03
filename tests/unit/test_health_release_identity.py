"""Pinned production health exposes only a verified runtime-v1 mirror identity."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mavedb_link.app as app_module
from mavedb_link.config import CacheSettings, MirrorConfig, ServerSettings
from mavedb_link.ingest.bundle import _expanded_tree_sha256
from mavedb_link.runtime_data_identity import RuntimeDataIdentityError


def _settings(tmp_path: Path, *, mutate_after_build: bool = False) -> ServerSettings:
    root = tmp_path / "reference"
    target = root / "compressed-sha"
    target.mkdir(parents=True)
    database = target / "mavedb.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE meta (id INTEGER PRIMARY KEY, schema_version INTEGER);
        INSERT INTO meta VALUES (1, 4);
        CREATE TABLE score_set (urn TEXT PRIMARY KEY, record_json TEXT NOT NULL);
        INSERT INTO score_set VALUES ('urn:mavedb:00000001', '{}');
        """
    )
    connection.close()
    database.chmod(0o444)
    digest = _expanded_tree_sha256(database, database.name)
    (target / "data-identity.json").write_text(
        json.dumps(
            {
                "release_tag": "data-2026-06-24-s4-r3",
                "compressed_sha256": "a" * 64,
                "expanded_sha256": digest,
                "schema_version": "4.0.0",
            }
        ),
        encoding="utf-8",
    )
    (root / "current").symlink_to(target.name)
    if mutate_after_build:
        database.chmod(0o644)
        with database.open("ab") as stream:
            stream.write(b"altered")
        database.chmod(0o444)
    mirror = MirrorConfig(
        data_dir=root / "current",
        reference_root=root,
        bundle_url=(
            "https://github.com/berntpopp/mavedb-link/releases/download/"
            "data-2026-06-24-s4-r3/mavedb.sqlite.zst"
        ),
        bundle_release_tag="data-2026-06-24-s4-r3",
        bundle_expected_sha256="a" * 64,
        bundle_expected_expanded_sha256=digest,
        bundle_expected_schema_version="4.0.0",
    )
    return ServerSettings(
        environment="production",
        mirror=mirror,
        cache=CacheSettings(db_path=tmp_path / "cache.sqlite"),
    )


def test_health_publishes_actual_runtime_identity_for_pinned_mirror(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured = _settings(tmp_path)
    monkeypatch.setattr(app_module, "settings", configured)
    expected = f"sha256:{configured.mirror.bundle_expected_expanded_sha256}"

    with TestClient(app_module.create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["data_available"] is True
    assert body["release_identity"] == {
        "schema_version": 1,
        "data_identity": {
            "expected": {
                "release_tag": "data-2026-06-24-s4-r3",
                "digest": expected,
            },
            "actual": {
                "release_tag": "data-2026-06-24-s4-r3",
                "digest": expected,
            },
        },
    }


def test_health_fails_closed_when_served_mirror_bytes_changed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(app_module, "settings", _settings(tmp_path, mutate_after_build=True))

    with (
        pytest.raises(RuntimeDataIdentityError, match="expanded database identity mismatch"),
        TestClient(app_module.create_app()),
    ):
        pytest.fail("an invalid pinned mirror must prevent the application from starting")


def test_health_does_not_treat_a_partial_pin_as_live_only(tmp_path: Path, monkeypatch) -> None:
    configured = ServerSettings(
        mirror=MirrorConfig(
            bundle_release_tag="data-2026-06-24-s4-r3",
            bundle_expected_sha256="a" * 64,
        ),
        cache=CacheSettings(db_path=tmp_path / "cache.sqlite"),
    )
    monkeypatch.setattr(app_module, "settings", configured)

    with (
        pytest.raises(RuntimeDataIdentityError, match="incomplete"),
        TestClient(app_module.create_app()),
    ):
        pytest.fail("a partial data pin must prevent the application from starting")


def test_health_leaves_explicit_live_only_mode_unpinned(tmp_path: Path, monkeypatch) -> None:
    configured = _settings(tmp_path)
    configured.mirror.enabled = False
    monkeypatch.setattr(app_module, "settings", configured)

    with TestClient(app_module.create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert "release_identity" not in response.json()
    assert "data_available" not in response.json()
