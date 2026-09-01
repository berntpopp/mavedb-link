"""Regression coverage for repeat immutable MaveDB data publication."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mavedb_link.ingest.release_identity import (
    IdentityComparisonError,
    ReleaseState,
    compare_release_identity,
    database_semantic_sha256,
)


def _write_metadata(
    path: Path, *, tag: str, build_revision: str = "d" * 40, **overrides: object
) -> Path:
    payload = {
        "tag": tag,
        "asset_sha256": "a" * 64,
        "asset_size": 12,
        "expanded_tree_sha256": "b" * 64,
        "expanded_size": 8192,
        "schema_version": "4.0.0",
        "build_revision": build_revision,
        "source_sha256": "c" * 64,
        "source_url": "https://zenodo.org/records/11201736/files/mavedb.zip",
        "retrieved_at": "2026-09-01T00:00:00Z",
        "score_set_count": 4,
        "mapped_variant_count": 8,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_semantic_database(
    path: Path, *, build_utc: str, build_duration_s: float, payload: str = "same"
) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """CREATE TABLE meta (
                id INTEGER PRIMARY KEY,
                schema_version INTEGER,
                dump_as_of TEXT,
                source_sha256 TEXT,
                source_url TEXT,
                score_set_count INTEGER,
                mapped_variant_count INTEGER,
                build_utc TEXT,
                build_duration_s REAL
            )"""
        )
        connection.execute("CREATE TABLE mirror_payload (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO meta VALUES (1, 4, '2026-06-24', ?, ?, 4, 8, ?, ?)",
            (
                "c" * 64,
                "https://zenodo.org/records/11201736/files/mavedb.zip",
                build_utc,
                build_duration_s,
            ),
        )
        connection.execute("INSERT INTO mirror_payload VALUES (1, ?)", (payload,))
        connection.commit()
    finally:
        connection.close()
    return path


def test_identical_bundle_rerun_ignores_candidate_build_revision(tmp_path: Path) -> None:
    """A verified prior bundle remains authoritative across later workflow reruns."""
    tag = "data-2026-06-24-s4-r3"
    current = _write_metadata(tmp_path / "current.json", tag=tag, build_revision="e" * 40)
    existing = _write_metadata(tmp_path / "existing.json", tag=tag)

    result = compare_release_identity(current, existing, expected_tag=tag)

    assert result.state is ReleaseState.PUBLISHED_NOOP
    assert result.differing_field is None


def test_rerun_accepts_only_equal_canonical_database_content(tmp_path: Path) -> None:
    """Volatile build timing may change bytes, but never release semantics."""
    tag = "data-2026-06-24-s4-r3"
    existing_database = _write_semantic_database(
        tmp_path / "existing.sqlite", build_utc="2026-08-31T19:52:11Z", build_duration_s=32.1
    )
    current_database = _write_semantic_database(
        tmp_path / "current.sqlite", build_utc="2026-09-01T11:36:07Z", build_duration_s=41.7
    )
    existing = _write_metadata(tmp_path / "existing.json", tag=tag)
    current = _write_metadata(
        tmp_path / "current.json",
        tag=tag,
        build_revision="e" * 40,
        asset_sha256="d" * 64,
        asset_size=13,
        expanded_tree_sha256="e" * 64,
        expanded_size=8193,
    )

    result = compare_release_identity(
        current,
        existing,
        expected_tag=tag,
        current_semantic_sha256=database_semantic_sha256(current_database),
        existing_database=existing_database,
    )

    assert result.state is ReleaseState.PUBLISHED_NOOP
    assert result.differing_field is None


def test_rerun_rejects_different_canonical_database_content(tmp_path: Path) -> None:
    tag = "data-2026-06-24-s4-r3"
    existing_database = _write_semantic_database(
        tmp_path / "existing.sqlite", build_utc="2026-08-31T19:52:11Z", build_duration_s=32.1
    )
    current_database = _write_semantic_database(
        tmp_path / "current.sqlite",
        build_utc="2026-09-01T11:36:07Z",
        build_duration_s=41.7,
        payload="different",
    )
    existing = _write_metadata(tmp_path / "existing.json", tag=tag)
    current = _write_metadata(
        tmp_path / "current.json",
        tag=tag,
        asset_sha256="d" * 64,
        asset_size=13,
        expanded_tree_sha256="e" * 64,
        expanded_size=8193,
    )

    result = compare_release_identity(
        current,
        existing,
        expected_tag=tag,
        current_database=current_database,
        existing_database=existing_database,
    )

    assert result.state is ReleaseState.COLLISION
    assert result.differing_field == "canonical_database_sha256"


def test_cli_accepts_transferred_canonical_digest_for_a_rerun(tmp_path: Path) -> None:
    tag = "data-2026-06-24-s4-r3"
    existing_database = _write_semantic_database(
        tmp_path / "existing.sqlite", build_utc="2026-08-31T19:52:11Z", build_duration_s=32.1
    )
    current_database = _write_semantic_database(
        tmp_path / "current.sqlite", build_utc="2026-09-01T11:36:07Z", build_duration_s=41.7
    )
    digest = tmp_path / "current.semantic.sha256"
    digest.write_text(f"{database_semantic_sha256(current_database)}\n", encoding="ascii")
    existing = _write_metadata(tmp_path / "existing.json", tag=tag)
    current = _write_metadata(
        tmp_path / "current.json",
        tag=tag,
        asset_sha256="d" * 64,
        asset_size=13,
        expanded_tree_sha256="e" * 64,
        expanded_size=8193,
    )

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "mavedb_link.ingest.release_identity",
            "compare",
            "--current",
            str(current),
            "--existing",
            str(existing),
            "--current-semantic-sha256",
            str(digest),
            "--existing-database",
            str(existing_database),
            "--expected-tag",
            tag,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"differing_field": None, "state": "published_noop"}


def test_revised_data_tag_is_valid_but_revision_zero_is_not(tmp_path: Path) -> None:
    tag = "data-2026-06-24-s4-r2"
    current = _write_metadata(tmp_path / "current.json", tag=tag)
    existing = _write_metadata(tmp_path / "existing.json", tag=tag)
    assert (
        compare_release_identity(current, existing, expected_tag=tag).state
        is ReleaseState.PUBLISHED_NOOP
    )

    invalid = _write_metadata(tmp_path / "invalid.json", tag="data-2026-06-24-s4-r0")
    with pytest.raises(IdentityComparisonError, match="tag"):
        compare_release_identity(invalid, existing, expected_tag=tag)
