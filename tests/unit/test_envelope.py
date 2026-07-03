"""Tests for the MCP envelope boundary: honest errors + uniform observability.

Covers the consumer-review Phase 0/1 contracts:
- GAP-2: an internal error while building a richer view is honest + retryable
  with a concrete "lower the response_mode" recovery, never an opaque string.
- GAP-5: every response's _meta carries elapsed_ms + truncated + token_estimate.
- 1.3: an over-budget response is flagged + steered, never silently oversized.
"""

from __future__ import annotations

from typing import Any

from mavedb_link.constants import RESPONSE_TOKEN_BUDGET
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool


async def _boom() -> dict[str, Any]:
    raise RuntimeError("kaboom internal detail that must not leak")


async def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return payload


# --- GAP-2: honest, teaching, retryable internal errors ------------------------


async def test_internal_error_at_standard_is_retryable_with_lower_mode() -> None:
    ctx = McpErrorContext(
        "get_variant_score", arguments={"urn": "urn:mavedb:00001242-a-1"}, response_mode="standard"
    )
    env = await run_mcp_tool("get_variant_score", _boom, context=ctx)
    assert env["success"] is False
    assert env["error_code"] == "internal_error"
    assert env["retryable"] is True
    assert env["recovery_action"] == "lower_response_mode"
    # Teaching message names the failing verbosity + the concrete remedy.
    assert "standard" in env["message"]
    assert "compact" in env["message"]
    # No raw traceback / internal detail leaks.
    assert "kaboom" not in env["message"]
    # A ready-to-run recovery: re-call the SAME tool at a lower verbosity.
    steps = env["_meta"]["next_commands"]
    assert steps[0]["tool"] == "get_variant_score"
    assert steps[0]["arguments"]["response_mode"] == "compact"


async def test_internal_error_at_compact_is_not_opaque() -> None:
    ctx = McpErrorContext("get_score_set", response_mode="compact")
    env = await run_mcp_tool("get_score_set", _boom, context=ctx)
    assert env["error_code"] == "internal_error"
    # Even the generic case is a teaching message, not the old opaque string.
    assert env["message"] != "An internal error occurred. The request was not completed."
    assert env["_meta"]["next_commands"]


# --- GAP-5: uniform observability in _meta -------------------------------------


async def test_meta_carries_uniform_observability_at_compact() -> None:
    env = await run_mcp_tool("get_score_set", lambda: _ok({"urn": "x"}), context=None)
    meta = env["_meta"]
    assert "elapsed_ms" in meta
    assert meta["truncated"] is False
    assert isinstance(meta["token_estimate"], int)
    assert "next_commands" not in meta  # no chainer attached here, but key absent is fine


async def test_meta_minimal_is_lean_but_observable() -> None:
    ctx = McpErrorContext("get_score_set", response_mode="minimal")
    env = await run_mcp_tool(
        "get_score_set",
        lambda: _ok({"urn": "x", "_meta": {"next_commands": [{"tool": "t"}]}}),
        context=ctx,
    )
    meta = env["_meta"]
    # Observability scalars are uniform...
    assert {"tool", "request_id", "elapsed_ms", "truncated", "token_estimate"} <= set(meta)
    # ...but guidance is the explicit opt-out at minimal.
    assert "next_commands" not in meta
    assert "capabilities_version" not in meta
    # The research-use disclaimer is not guidance -- it survives minimal too.
    assert meta["unsafe_for_clinical_use"] is True


async def test_meta_truncated_reflects_body_flag() -> None:
    env = await run_mcp_tool(
        "get_variant_scores", lambda: _ok({"rows": [], "truncated": True}), context=None
    )
    assert env["_meta"]["truncated"] is True


# --- 1.3: token budget guard ---------------------------------------------------


async def test_budget_guard_flags_and_steers_oversized_response() -> None:
    big = {"rows": ["x" * 50 for _ in range(RESPONSE_TOKEN_BUDGET)]}  # well over budget
    ctx = McpErrorContext("get_variant_scores", arguments={"urn": "u"}, response_mode="standard")
    env = await run_mcp_tool("get_variant_scores", lambda: _ok(big), context=ctx)
    meta = env["_meta"]
    assert meta["token_estimate"] > RESPONSE_TOKEN_BUDGET
    assert meta["truncated"] is True
    assert meta["budget_exceeded"] is True
    assert "steer" in meta and "response_mode" in meta["steer"]


# --- A.2: the budget ceiling ENFORCES on a list page (deterministic truncation) --


async def test_budget_guard_trims_oversized_list_page_to_fit() -> None:
    # GAP-A.2: a list page over the 25k cap must be deterministically trimmed (not
    # just flagged) -- reduce `returned`, drop trailing rows, keep the response
    # re-pageable -- so the front-door search tool returns data, never a client
    # rejection. A page carries the pagination contract (returned/offset/next_offset).
    big = [{"urn": f"urn:mavedb:{i:08d}-a-1", "blob": "x" * 600} for i in range(200)]
    page = {
        "query": "BRCA1",
        "results": big,
        "total": 200,
        "returned": 200,
        "limit": 200,
        "offset": 0,
        "truncated": False,
        "next_offset": None,
    }
    ctx = McpErrorContext("search_score_sets", arguments={"text": "BRCA1"}, response_mode="compact")
    env = await run_mcp_tool("search_score_sets", lambda: _ok(dict(page)), context=ctx)
    meta = env["_meta"]
    assert meta["budget_exceeded"] is True
    assert meta["truncated"] is True
    # Trimmed deterministically: fewer rows, and the payload now fits the cap.
    assert env["returned"] < 200
    assert env["returned"] == len(env["results"]) > 0
    assert meta["token_estimate"] <= RESPONSE_TOKEN_BUDGET
    # Honest + re-pageable: the dropped rows remain reachable via the real total.
    assert env["total"] == 200
    assert env["truncated"] is True
    assert env["next_offset"] == env["returned"]


async def test_budget_guard_updates_next_start_alias_when_trimming() -> None:
    # get_variant_scores mirrors offset/next_offset onto start/next_start, so the
    # trim must keep that alias consistent for callers paging by start=.
    rows = [{"accession": f"urn:mavedb:00000001-a-1#{i}", "blob": "y" * 600} for i in range(200)]
    page = {
        "urn": "urn:mavedb:00000001-a-1",
        "columns": ["accession", "blob"],
        "rows": rows,
        "total": 5000,
        "returned": 200,
        "start": 0,
        "offset": 0,
        "limit": 200,
        "truncated": True,
        "next_start": 200,
        "next_offset": 200,
    }
    ctx = McpErrorContext("get_variant_scores", arguments={"urn": "u"}, response_mode="standard")
    env = await run_mcp_tool("get_variant_scores", lambda: _ok(dict(page)), context=ctx)
    assert env["returned"] == len(env["rows"]) < 200
    assert env["next_start"] == env["returned"]
    assert env["next_offset"] == env["returned"]
    assert env["_meta"]["token_estimate"] <= RESPONSE_TOKEN_BUDGET


async def test_budget_guard_does_not_trim_a_record_payload() -> None:
    # A record has no pagination contract; trimming it would corrupt the structured
    # output, so it stays flagged + steered with its data intact.
    record = {"urn": "urn:mavedb:00000001-a-1", "method_text": "y" * 200_000}
    ctx = McpErrorContext("get_score_set", arguments={"urn": "u"}, response_mode="full")
    env = await run_mcp_tool("get_score_set", lambda: _ok(dict(record)), context=ctx)
    meta = env["_meta"]
    assert meta["budget_exceeded"] is True
    assert env["method_text"] == "y" * 200_000  # untouched
    assert meta["token_estimate"] > RESPONSE_TOKEN_BUDGET
