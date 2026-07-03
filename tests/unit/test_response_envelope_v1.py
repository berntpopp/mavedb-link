"""Locking test for the GeneFoundry Response-Envelope Standard v1 (flat banner).

Ratified contract (see genefoundry-router's
``docs/RESPONSE-ENVELOPE-STANDARD-v1.md``):

- SUCCESS: ``{"success": True, <payload>, "_meta": {..., "unsafe_for_clinical_use":
  True}}``.
- FAILURE: a FLAT in-band dict -- ``{"success": False, "error_code": <str>,
  "message": <str>, "retryable": <bool>, "recovery_action": <str>, "_meta": {"tool":
  ..., "unsafe_for_clinical_use": True}}`` -- never a bare exception, never a nested
  ``error: {}`` shape.

mavedb-link builds both shapes at its MCP wrapper boundary in
``mavedb_link.mcp.envelope``: the async ``run_mcp_tool(...)`` wraps every tool body,
injecting ``success``/``_meta`` on the return value on success, and converting any
raised exception into the flat error dict via the module-private ``_error_envelope``
helper (exercised here only indirectly, through ``run_mcp_tool``'s except branch,
since it is not part of the public API).

This test locks the envelope shape mavedb-link ships today, including the per-call
research-use disclaimer: ``_meta.unsafe_for_clinical_use`` is now stamped ``True`` on
BOTH the success and the error path, at EVERY ``response_mode`` -- including
``minimal``, which otherwise strips ``_meta`` down to the bare observability scalars
(``tool``/``request_id``/``elapsed_ms``/``truncated``/``token_estimate``). The
disclaimer is special-cased in ``envelope._MANDATORY_META_KEYS`` so it survives that
filter; see the ``minimal``-mode test below.
"""

from __future__ import annotations

from typing import Any

from mavedb_link.exceptions import NotFoundError
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool

# A real registered tool (mavedb_link/mcp/tools/score_sets.py::get_score_set) so the
# envelope is exercised with a genuine tool name, not a placeholder.
_REAL_TOOL = "get_score_set"


async def test_success_envelope_is_flat_banner_with_uniform_meta() -> None:
    """SUCCESS: top-level ``success: True`` beside the payload, plus a ``_meta`` block.

    Locks the fields ``run_mcp_tool`` actually stamps into ``_meta`` on every
    successful call: ``tool``, ``request_id``, ``elapsed_ms``, ``truncated``, and
    ``token_estimate`` (GAP-5 observability contract).
    """

    async def call() -> dict[str, Any]:
        return {"urn": "urn:mavedb:00000001-a-1", "title": "Example score set"}

    ctx = McpErrorContext(_REAL_TOOL, arguments={"urn": "urn:mavedb:00000001-a-1"})
    env = await run_mcp_tool(_REAL_TOOL, call, context=ctx)

    # Flat banner: success + payload live at the SAME top level, no nesting.
    assert env["success"] is True
    assert env["urn"] == "urn:mavedb:00000001-a-1"
    assert env["title"] == "Example score set"

    meta = env["_meta"]
    assert meta["tool"] == _REAL_TOOL
    assert isinstance(meta["request_id"], str) and meta["request_id"]
    assert isinstance(meta["elapsed_ms"], int)
    assert meta["truncated"] is False
    assert isinstance(meta["token_estimate"], int)

    # Fleet Response-Envelope Standard v1: the per-call research-use disclaimer is
    # an in-band _meta flag on every successful response, not just static text.
    assert meta["unsafe_for_clinical_use"] is True


async def test_success_envelope_carries_disclaimer_at_minimal_mode() -> None:
    """``minimal`` strips ``_meta`` to bare observability -- the disclaimer survives.

    ``minimal`` is the guidance opt-out (drops ``next_commands``,
    ``capabilities_version``), but the safety disclaimer is not guidance a caller
    can opt out of, so it must still be present.
    """

    async def call() -> dict[str, Any]:
        return {"urn": "urn:mavedb:00000001-a-1", "title": "Example score set"}

    ctx = McpErrorContext(
        _REAL_TOOL,
        arguments={"urn": "urn:mavedb:00000001-a-1"},
        response_mode="minimal",
    )
    env = await run_mcp_tool(_REAL_TOOL, call, context=ctx)

    meta = env["_meta"]
    assert meta["unsafe_for_clinical_use"] is True
    # Confirms the guidance opt-out still holds -- only the disclaimer is exempted.
    assert "next_commands" not in meta
    assert "capabilities_version" not in meta


async def test_error_envelope_is_flat_never_nested() -> None:
    """FAILURE: a flat in-band dict, never a bare exception or nested ``error: {}}``.

    Drives the REAL error path -- a domain exception (``NotFoundError``) raised
    inside a tool body, caught by ``run_mcp_tool``'s except branch -- rather than
    calling the module-private ``_error_envelope`` helper directly.
    """

    async def call() -> dict[str, Any]:
        raise NotFoundError("No matching MaveDB record found for urn:mavedb:99999999-a-1.")

    ctx = McpErrorContext(_REAL_TOOL, arguments={"urn": "urn:mavedb:99999999-a-1"})
    env = await run_mcp_tool(_REAL_TOOL, call, context=ctx)

    assert env["success"] is False
    assert isinstance(env["error_code"], str) and env["error_code"] == "not_found"
    assert isinstance(env["message"], str) and env["message"]
    assert isinstance(env["retryable"], bool)
    assert env["retryable"] is False
    assert isinstance(env["recovery_action"], str) and env["recovery_action"]
    assert env["recovery_action"] == "reformulate_input"

    # FLAT: no nested "error" object anywhere alongside the top-level fields.
    assert "error" not in env

    meta = env["_meta"]
    assert meta["tool"] == _REAL_TOOL

    # Same fleet standard as the success path: the disclaimer is on the error
    # `_meta` too, not just success responses.
    assert meta["unsafe_for_clinical_use"] is True


async def test_error_envelope_carries_disclaimer_at_minimal_mode() -> None:
    """The disclaimer survives ``minimal``-mode tiering on the error path too."""

    async def call() -> dict[str, Any]:
        raise NotFoundError("No matching MaveDB record found for urn:mavedb:99999999-a-1.")

    ctx = McpErrorContext(
        _REAL_TOOL,
        arguments={"urn": "urn:mavedb:99999999-a-1"},
        response_mode="minimal",
    )
    env = await run_mcp_tool(_REAL_TOOL, call, context=ctx)

    meta = env["_meta"]
    assert meta["unsafe_for_clinical_use"] is True
    assert "next_commands" not in meta
