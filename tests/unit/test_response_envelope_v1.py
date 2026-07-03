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

This test locks the envelope shape mavedb-link ACTUALLY ships today. It intentionally
does NOT assert ``unsafe_for_clinical_use`` is present: as of this writing neither the
success nor the error path stamps that key anywhere in ``_meta`` -- see the drift note
below each test.
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

    # DRIFT vs the ideal Response-Envelope Standard v1 contract: the standard calls
    # for `_meta.unsafe_for_clinical_use: True` on every response. mavedb-link does
    # not stamp this key anywhere today -- the research-use disclaimer is only
    # static text (mavedb_link/mcp/resources.py, README.md), never an in-band _meta
    # flag. Locking the current (non-conformant) shape here so any future addition
    # of the key is a deliberate, visible change to this test.
    assert "unsafe_for_clinical_use" not in meta


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

    # Same drift as the success path: no `unsafe_for_clinical_use` key on the error
    # `_meta` either. Locking ground truth, not the ideal contract.
    assert "unsafe_for_clinical_use" not in meta
