"""MaveDB runtime identity is derived from the materialized mirror bytes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from mavedb_link import data_probe
from mavedb_link.ingest.bundle import _expanded_tree_sha256
from mavedb_link.runtime_data_identity import (
    RuntimeDataIdentityError,
    query_data_probe,
    runtime_identity_envelope,
    verify_runtime_data_identity,
    verify_settings_runtime_identity,
)


def _materialized_reference(tmp_path: Path) -> tuple[Path, Path, str]:
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
        INSERT INTO score_set VALUES ('urn:mavedb:00000002', '{}');
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
    return root, root / "current" / database.name, digest


def test_runtime_identity_verifies_served_database_and_returns_data_probe(
    tmp_path: Path,
) -> None:
    root, database, digest = _materialized_reference(tmp_path)

    identity = verify_runtime_data_identity(
        database,
        reference_root=root,
        expected_release_tag="data-2026-06-24-s4-r3",
        expected_compressed_sha256="a" * 64,
        expected_expanded_sha256=digest,
        expected_schema_version="4.0.0",
    )

    assert identity == {
        "release_tag": "data-2026-06-24-s4-r3",
        "digest": f"sha256:{digest}",
    }
    probe = query_data_probe(database)
    assert probe == {
        "data_schema_version": "4.0.0",
        "record_count": 2,
        "query_result_sha256": hashlib.sha256(b"urn:mavedb:00000001").hexdigest(),
    }


def test_runtime_identity_rejects_changed_served_database_bytes(tmp_path: Path) -> None:
    root, database, digest = _materialized_reference(tmp_path)
    database.chmod(0o644)
    with database.open("ab") as stream:
        stream.write(b"changed")
    database.chmod(0o444)

    with pytest.raises(RuntimeDataIdentityError, match="expanded database identity mismatch"):
        verify_runtime_data_identity(
            database,
            reference_root=root,
            expected_release_tag="data-2026-06-24-s4-r3",
            expected_compressed_sha256="a" * 64,
            expected_expanded_sha256=digest,
            expected_schema_version="4.0.0",
        )


def test_runtime_identity_rejects_database_outside_active_reference(tmp_path: Path) -> None:
    root, database, digest = _materialized_reference(tmp_path)
    other = tmp_path / "other.sqlite"
    other.write_bytes(database.read_bytes())

    with pytest.raises(RuntimeDataIdentityError, match="active materialized reference"):
        verify_runtime_data_identity(
            other,
            reference_root=root,
            expected_release_tag="data-2026-06-24-s4-r3",
            expected_compressed_sha256="a" * 64,
            expected_expanded_sha256=digest,
            expected_schema_version="4.0.0",
        )


def test_runtime_identity_rejects_wrong_release_tag_in_materializer_record(
    tmp_path: Path,
) -> None:
    root, database, digest = _materialized_reference(tmp_path)
    identity_path = database.parent / "data-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["release_tag"] = "data-2026-02-06"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")

    with pytest.raises(RuntimeDataIdentityError, match="release tag mismatch"):
        verify_runtime_data_identity(
            database,
            reference_root=root,
            expected_release_tag="data-2026-06-24-s4-r3",
            expected_compressed_sha256="a" * 64,
            expected_expanded_sha256=digest,
            expected_schema_version="4.0.0",
        )


def test_semantic_probe_cli_prints_only_the_closed_probe_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, database, _ = _materialized_reference(tmp_path)
    monkeypatch.setattr(
        data_probe,
        "settings",
        SimpleNamespace(mirror=SimpleNamespace(db_path=database)),
    )

    data_probe.main()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "data_schema_version": "4.0.0",
        "record_count": 2,
        "query_result_sha256": hashlib.sha256(b"urn:mavedb:00000001").hexdigest(),
    }


def test_release_manifest_pins_the_published_provenance_complete_bundle() -> None:
    config = json.loads(Path("container-release.json").read_text(encoding="utf-8"))

    assert config["data_identity_contract"] == "runtime-v1"
    assert config["data"]["mode"] == "external-reference"
    assert config["data"]["release_tag"] == "data-2026-06-24-s4-r3"
    assert config["data"]["schema_compatibility"] == ["4.0.0"]
    assert config["data"]["digest"] == (
        "sha256:5f2315d8036eca4c93574238f261e22d5b5199f45c370f58a0455616ad7d26b9"
    )


def test_runtime_health_envelope_refuses_an_unmatched_actual_identity() -> None:
    with pytest.raises(RuntimeDataIdentityError, match="does not match the pinned release"):
        runtime_identity_envelope(
            "data-2026-06-24-s4-r3",
            "5f2315d8036eca4c93574238f261e22d5b5199f45c370f58a0455616ad7d26b9",
            {"release_tag": "data-2026-02-06", "digest": "sha256:" + "0" * 64},
        )


def test_partial_runtime_pin_is_not_treated_as_unpinned() -> None:
    settings = SimpleNamespace(
        mirror=SimpleNamespace(
            enabled=True,
            bundle_release_tag="data-2026-06-24-s4-r3",
            bundle_url=(
                "https://github.com/berntpopp/mavedb-link/releases/download/"
                "data-2026-06-24-s4-r3/mavedb.sqlite.zst"
            ),
            bundle_path=None,
            bundle_expected_sha256="a" * 64,
            bundle_expected_expanded_sha256=None,
            bundle_expected_schema_version="4.0.0",
            db_path=Path("missing.sqlite"),
            reference_root=Path("missing-reference"),
        )
    )

    with pytest.raises(RuntimeDataIdentityError, match="incomplete"):
        verify_settings_runtime_identity(settings)
