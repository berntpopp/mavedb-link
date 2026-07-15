"""Tool-Surface Budget v1: output_schema is suppressed, structuredContent kept.

The tools no longer advertise an ``outputSchema`` (it is optional in MCP, no model
reads it, and it was ~40% of this server's surface). Suppressing it must NOT lose
``structuredContent`` -- every tool returns a dict envelope, so FastMCP still emits
structured content. These tests lock both halves of that contract: no tool ships an
output schema, yet every success AND every error still carries a structured dict
(and an error carries protocol ``isError:true``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import respx

from tests import fixtures
from tests.unit.test_tools_e2e import _mock_all

BASE = fixtures.BASE_URL

_CALLS: dict[str, dict[str, Any]] = {
    "get_server_capabilities": {"detail": "full"},
    "get_diagnostics": {},
    "search_score_sets": {"text": "UBE2I"},
    "get_score_set": {"urn": fixtures.SCORE_SET_URN},
    "get_variant_scores": {"urn": fixtures.SCORE_SET_URN},
    "get_variant_score": {"urn": fixtures.VARIANT_URN},
    "get_gene_score_sets": {"symbol": "UBE2I"},
    "get_experiment": {"urn": fixtures.EXPERIMENT_URN},
    "search_experiments": {"text": "UBE2I"},
    "get_mapped_variants": {"urn": fixtures.SCORE_SET_URN},
    "get_collection": {"urn": fixtures.COLLECTION_URN},
    # find_variant collapsed vrs_id/variant_urn/hgvs into `variant`; the old names
    # remain accepted as aliases, exercised here.
    "find_variant": {"vrs_id": fixtures.VRS_ID},
    "get_hgvs_validation": {"variant": "NM_000059.4:c.8167G>A"},
    "get_classified_variants": {"urn": fixtures.SCORE_SET_URN},
    "get_score_distribution": {"urn": fixtures.SCORE_SET_URN},
}


async def _aschemas(facade: Any) -> dict[str, Any]:
    tools = await facade.list_tools()
    return {t.name: t.output_schema for t in tools}


def test_no_tool_advertises_an_output_schema(facade: Any) -> None:
    schemas = asyncio.run(_aschemas(facade))
    assert set(schemas) == set(_CALLS)
    advertised = {name for name, schema in schemas.items() if schema is not None}
    assert not advertised, f"these tools still ship an output schema: {sorted(advertised)}"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_all_tools_emit_structured_content_in_all_modes(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    modes = ["minimal", "compact", "standard", "full"]
    for name, base_args in _CALLS.items():
        supports_mode = name not in ("get_server_capabilities", "get_diagnostics")
        for mode in modes if supports_mode else [None]:
            args = dict(base_args)
            if mode is not None:
                args["response_mode"] = mode
            res = await facade.call_tool(name, args)
            payload = structured(res)
            # Suppressing output_schema must not lose structuredContent (dict return).
            assert isinstance(payload, dict) and payload, f"{name}/{mode}: no structured content"
            assert payload["success"] is True
            assert res.is_error is False


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_error_still_carries_structured_content_and_iserror(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    respx_mock.get("/score-sets/urn:mavedb:09999999-a-1").mock(
        return_value=httpx.Response(404, json={"detail": "missing"})
    )
    res = await facade.call_tool("get_score_set", {"urn": "urn:mavedb:09999999-a-1"})
    payload = structured(res)
    assert payload["success"] is False
    assert payload["error_code"] == "not_found"
    # Response-Envelope v1: an error envelope carries protocol isError:true AND the
    # machine-readable structured envelope (both, not one).
    assert res.is_error is True
