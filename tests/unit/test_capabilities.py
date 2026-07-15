"""Tests for the capabilities discovery surface and content-hash stability."""

from __future__ import annotations

from mavedb_link.constants import ERROR_CODES
from mavedb_link.mcp.capabilities import (
    TOOLS,
    build_capabilities,
    capabilities_version,
    project_capabilities,
)


def test_tools_unique_and_counted() -> None:
    assert len(TOOLS) == len(set(TOOLS))
    caps = build_capabilities()
    assert caps["tool_count"] == len(TOOLS)
    assert caps["tools"] == TOOLS


def test_error_taxonomy_complete() -> None:
    caps = build_capabilities()
    assert caps["error_codes"] == ERROR_CODES
    # The CLOSED six-value fleet enum (Response-Envelope Standard v1).
    assert len(ERROR_CODES) == 6
    assert set(ERROR_CODES) == {
        "invalid_input",
        "not_found",
        "ambiguous_query",
        "upstream_unavailable",
        "rate_limited",
        "internal",
    }


def test_capabilities_version_is_stable_content_hash() -> None:
    caps = build_capabilities()
    version = caps["capabilities_version"]
    assert isinstance(version, str)
    assert len(version) == 16
    # Stable across calls; the cached accessor agrees with a fresh build.
    assert capabilities_version() == version
    assert build_capabilities()["capabilities_version"] == version


def test_capabilities_version_excludes_build() -> None:
    caps = build_capabilities()
    # The hash must not depend on volatile build provenance.
    mutated = dict(caps)
    mutated["build"] = {"git_sha": "deadbeef", "built_at": "2099-01-01T00:00:00Z"}
    from mavedb_link.mcp.capabilities import _hash_contract

    assert _hash_contract(mutated) == _hash_contract(caps)


def test_summary_is_subset_of_full() -> None:
    summary = project_capabilities("summary")
    full = project_capabilities("full")
    assert summary["detail"] == "summary"
    assert full["detail"] == "full"
    assert set(summary).issubset(set(full) | {"detail", "more"})
    assert "more" in summary


def test_limits_advertised() -> None:
    caps = build_capabilities()
    assert caps["limits"]["max_search_limit"] == 100
    assert caps["read_only"] is True
    assert caps["research_use_only"] is True


def test_calibration_surface_advertised() -> None:
    # A4: discovery names WHICH response field carries calibrations on each tool.
    caps = build_capabilities()
    surface = caps["calibration_surface"]
    assert "score_calibrations" in surface["get_score_set"]
    assert "classification" in surface["get_variant_scores"]
    assert "classifications" in surface["get_variant_score"]
    # and it is part of the default summary projection
    assert "calibration_surface" in project_capabilities("summary")


def test_tool_hints_machine_readable() -> None:
    # 4.3: the hard-won usage rules are exposed as a machine-readable per-tool block,
    # not just buried in prose. Every hinted tool is a real registered tool.
    caps = build_capabilities()
    hints = caps["tool_hints"]
    assert set(hints).issubset(set(TOOLS))
    # The single tip that made the consumer's first lookup succeed (SGE null hgvs_pro).
    assert any("hgvs_pro" in h for h in hints["get_variant_score"])
    assert "tool_hints" in project_capabilities("summary")


def test_response_token_budget_advertised() -> None:
    from mavedb_link.constants import RESPONSE_TOKEN_BUDGET

    caps = build_capabilities()
    assert caps["response_token_budget"] == RESPONSE_TOKEN_BUDGET
    assert "response_token_budget" in project_capabilities("summary")
