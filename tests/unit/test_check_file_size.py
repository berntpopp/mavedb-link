"""Regression tests for the local file-size budget gate."""

from __future__ import annotations

from pathlib import Path

from scripts import check_file_size


def test_vendored_behaviour_gate_is_exempt_from_loc_budget() -> None:
    """The router-owned conformance gate is copied byte-for-byte, not split locally."""
    gate = Path("tests/conformance/behaviour.py")
    lines = gate.read_text(encoding="utf-8").count("\n") + 1

    assert lines > check_file_size.DEFAULT_LIMIT
    assert check_file_size.main() == 0
