"""The README '## Tools' table must match the registered tool surface exactly.

README Standard v1 §6: the table is machine-verified, not hand-maintained. Adding
a tool without adding its row (or removing one without removing its row) fails CI.

The live tool list is enumerated exactly as ``test_tool_names.py`` does it --
``create_mavedb_mcp()`` + ``list_tools()`` -- so this test cannot drift from the
real server surface.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from mavedb_link.mcp.facade import create_mavedb_mcp

README = Path(__file__).resolve().parents[2] / "README.md"

#: A table row's first cell, when it is a `backticked_tool_name`.
_TOOL_ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


@pytest.fixture
def registered_tools() -> set[str]:
    mcp = create_mavedb_mcp()
    tools: list[Any] = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


def _tools_section(text: str) -> str:
    """Return the body of the '## Tools' section (up to the next H2)."""
    match = re.search(r"^## Tools\s*$(.*?)^## ", text, re.M | re.S)
    assert match, "README has no '## Tools' section"
    return match.group(1)


@pytest.fixture
def readme_table_tools() -> list[str]:
    section = _tools_section(README.read_text(encoding="utf-8"))
    return [m.group(1) for line in section.splitlines() if (m := _TOOL_ROW.match(line))]


def test_readme_table_matches_registered_tools(
    readme_table_tools: list[str], registered_tools: set[str]
) -> None:
    documented = set(readme_table_tools)
    missing = registered_tools - documented
    extra = documented - registered_tools
    assert not missing, f"tools registered but absent from the README table: {sorted(missing)}"
    assert not extra, f"README table lists tools the server does not register: {sorted(extra)}"
    assert documented == registered_tools


def test_readme_table_has_no_duplicate_rows(readme_table_tools: list[str]) -> None:
    assert len(readme_table_tools) == len(set(readme_table_tools)), (
        f"duplicate rows in the README Tools table: {readme_table_tools}"
    )


def test_every_readme_row_has_a_purpose(readme_table_tools: list[str]) -> None:
    """A row is only useful if its second cell says what the tool is for."""
    section = _tools_section(README.read_text(encoding="utf-8"))
    for line in section.splitlines():
        match = _TOOL_ROW.match(line)
        if not match:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) >= 2, f"row for {match.group(1)!r} has no Purpose cell"
        assert cells[1], f"row for {match.group(1)!r} has an empty Purpose cell"
