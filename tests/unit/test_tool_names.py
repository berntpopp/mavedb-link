"""Tool-Naming Standard v1 conformance for the registered tool surface."""

from __future__ import annotations

import re
from typing import Any

import pytest

from mavedb_link.mcp.capabilities import TOOLS
from mavedb_link.mcp.facade import create_mavedb_mcp

#: Canonical verbs allowed for a read-only data backend (Tool-Naming Standard v1).
CANONICAL_VERBS = ("get", "search", "list", "resolve", "find", "compare", "compute")

#: Names that must NOT be exposed (cache management, etc.).
FORBIDDEN = {"clear_cache", "close", "aclose", "reset"}


@pytest.fixture
def tool_names() -> list[str]:
    import asyncio

    mcp = create_mavedb_mcp()
    return sorted(t.name for t in asyncio.run(mcp.list_tools()))


@pytest.fixture
def tool_schemas() -> dict[str, dict[str, Any]]:
    import asyncio

    mcp = create_mavedb_mcp()
    return {t.name: dict(t.parameters or {}) for t in asyncio.run(mcp.list_tools())}


def test_registered_equals_frozen_tools(tool_names: list[str]) -> None:
    assert tool_names == sorted(TOOLS)


def test_name_shape(tool_names: list[str]) -> None:
    for name in tool_names:
        assert re.fullmatch(r"[a-z0-9_]{1,50}", name), f"bad name shape: {name}"


def test_canonical_verb_prefix(tool_names: list[str]) -> None:
    for name in tool_names:
        assert name.split("_")[0] in CANONICAL_VERBS, f"non-canonical verb: {name}"


def test_no_self_namespace_prefix(tool_names: list[str]) -> None:
    # Names must compose behind the router as mavedb_<tool>; never self-prefix.
    for name in tool_names:
        assert not name.startswith("mavedb_"), f"self-prefixed: {name}"


def test_no_forbidden_names(tool_names: list[str]) -> None:
    assert FORBIDDEN.isdisjoint(set(tool_names))


def test_combined_router_name_under_64_chars(tool_names: list[str]) -> None:
    for name in tool_names:
        assert len(f"mavedb_{name}") <= 64


def test_every_tool_is_annotated_read_only() -> None:
    # 4.2: this is an entirely read-only server -- every tool advertises
    # readOnlyHint so hosts can surface safety without parsing prose.
    import asyncio

    mcp = create_mavedb_mcp()
    tools = asyncio.run(mcp.list_tools())
    assert tools
    for tool in tools:
        ann = getattr(tool, "annotations", None)
        read_only = getattr(ann, "readOnlyHint", None) if ann is not None else None
        assert read_only is True, f"{tool.name} is missing readOnlyHint"


def test_router_canonical_gene_schema_names(tool_schemas: dict[str, dict[str, Any]]) -> None:
    gene_schema = tool_schemas["get_gene_score_sets"]
    gene_props = gene_schema["properties"]
    assert "gene_symbol" in gene_props
    assert "symbol" not in gene_props
    assert gene_schema["required"] == ["gene_symbol"]

    find_schema = tool_schemas["find_variant"]
    find_props = find_schema["properties"]
    assert "gene_symbol" in find_props
    assert "gene" not in find_props


def test_variant_scores_exposes_offset_with_start_alias_rationale(
    tool_schemas: dict[str, dict[str, Any]],
) -> None:
    schema = tool_schemas["get_variant_scores"]
    props = schema["properties"]
    assert "offset" in props
    assert "start" not in props
    assert "start alias" in props["offset"]["description"]
