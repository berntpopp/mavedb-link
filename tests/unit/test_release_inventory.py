"""Bounded truth table for GitHub's release inventory projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mavedb_link.ingest.release_identity import (
    IdentityComparisonError,
    inspect_release_inventory,
)

TAG = "data-2026-06-24-s4-r2"


def _inventory(path: Path, entries: list[object]) -> Path:
    path.write_text("".join(f"{json.dumps(entry)}\n" for entry in entries), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], {"present": False, "release_id": None}),
        ([{"tag_name": "v0.5.5", "id": 7}], {"present": False, "release_id": None}),
        ([{"tag_name": TAG, "id": 379964682}], {"present": True, "release_id": 379964682}),
    ],
)
def test_release_inventory_classifies_absent_wrong_and_exact_tags(
    tmp_path: Path, entries: list[object], expected: dict[str, object]
) -> None:
    inventory = _inventory(tmp_path / "inventory.jsonl", entries)

    assert inspect_release_inventory(inventory, expected_tag=TAG) == expected


def test_release_inventory_rejects_duplicate_exact_tags(tmp_path: Path) -> None:
    inventory = _inventory(
        tmp_path / "inventory.jsonl",
        [{"tag_name": TAG, "id": 1}, {"tag_name": TAG, "id": 2}],
    )

    with pytest.raises(IdentityComparisonError, match="one exact"):
        inspect_release_inventory(inventory, expected_tag=TAG)


@pytest.mark.parametrize(
    "entry",
    [
        None,
        [],
        {"tag_name": TAG},
        {"tag_name": TAG, "id": 0},
        {"tag_name": TAG, "id": True},
        {"tag_name": TAG, "id": 1, "draft": True},
        {"tag_name": 1, "id": 1},
    ],
)
def test_release_inventory_rejects_malformed_projections(tmp_path: Path, entry: object) -> None:
    inventory = _inventory(tmp_path / "inventory.jsonl", [entry])

    with pytest.raises(IdentityComparisonError, match="inventory"):
        inspect_release_inventory(inventory, expected_tag=TAG)


def test_release_inventory_is_bounded_to_ten_full_api_pages(tmp_path: Path) -> None:
    entries = [{"tag_name": f"v{index}", "id": index + 1} for index in range(1001)]
    inventory = _inventory(tmp_path / "inventory.jsonl", entries)

    with pytest.raises(IdentityComparisonError, match="1000"):
        inspect_release_inventory(inventory, expected_tag=TAG)
