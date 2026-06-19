"""Tool-Naming Standard v1 conformance for the registered tool surface."""

from __future__ import annotations

import re

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
