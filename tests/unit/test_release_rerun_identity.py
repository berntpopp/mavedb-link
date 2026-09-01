"""Regression coverage for repeat immutable MaveDB data publication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mavedb_link.ingest.release_identity import (
    IdentityComparisonError,
    ReleaseState,
    compare_release_identity,
)


def _write_metadata(path: Path, *, tag: str, build_revision: str = "d" * 40) -> Path:
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
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_identical_bundle_rerun_ignores_candidate_build_revision(tmp_path: Path) -> None:
    """A verified prior bundle remains authoritative across later workflow reruns."""
    tag = "data-2026-06-24-s4-r3"
    current = _write_metadata(tmp_path / "current.json", tag=tag, build_revision="e" * 40)
    existing = _write_metadata(tmp_path / "existing.json", tag=tag)

    result = compare_release_identity(current, existing, expected_tag=tag)

    assert result.state is ReleaseState.PUBLISHED_NOOP
    assert result.differing_field is None


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
