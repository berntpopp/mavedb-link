"""F.2/G2-G3: tool parameters carry detailed descriptions + declared value sets."""

from __future__ import annotations

import asyncio
from typing import Any

from mavedb_link.constants import TARGET_CATEGORIES
from mavedb_link.mcp.facade import create_mavedb_mcp


def _properties(tool_name: str) -> dict[str, Any]:
    mcp = create_mavedb_mcp()
    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == tool_name)
    return (tool.parameters or {}).get("properties", {})


def test_target_types_declares_its_fixed_categories() -> None:
    # G3: a fixed value set is declared, not left to prose -- the agent should see
    # the three MaveDB target categories without guessing.
    blob = str(_properties("search_score_sets").get("target_types", {}))
    assert all(category in blob for category in TARGET_CATEGORIES)


def test_facet_list_params_carry_examples() -> None:
    # G2: every facet param shows a concrete example, not the generic placeholder.
    props = _properties("search_score_sets")
    for name in ("targets", "target_organism_names", "authors"):
        assert props[name].get("examples"), f"{name} has no example"
