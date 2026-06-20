"""Validate every tool's output against its declared output_schema.

Drives each tool across response modes (and an error case) and validates the
returned structured content with a JSON-Schema validator — the gate against a
payload field leaking past its permissive schema.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import respx
from jsonschema import Draft202012Validator

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
    "find_variant": {"vrs_id": fixtures.VRS_ID},
    "get_hgvs_validation": {"variant": "NM_000059.4:c.8167G>A"},
    "get_classified_variants": {"urn": fixtures.SCORE_SET_URN},
}


async def _aschemas(facade: Any) -> dict[str, dict[str, Any]]:
    tools = await facade.list_tools()
    return {t.name: t.output_schema for t in tools if t.output_schema}


def _schemas(facade: Any) -> dict[str, dict[str, Any]]:
    return asyncio.run(_aschemas(facade))


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_all_tools_validate_in_all_modes(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    schemas = await _aschemas(facade)
    modes = ["minimal", "compact", "standard", "full"]
    for name, base_args in _CALLS.items():
        schema = schemas[name]
        validator = Draft202012Validator(schema)
        supports_mode = name not in ("get_server_capabilities", "get_diagnostics")
        for mode in modes if supports_mode else [None]:
            args = dict(base_args)
            if mode is not None:
                args["response_mode"] = mode
            res = await facade.call_tool(name, args)
            payload = structured(res)
            errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
            assert not errors, f"{name}/{mode}: {[e.message for e in errors]}"
            assert payload["success"] is True


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_error_envelope_validates(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    respx_mock.get("/score-sets/urn:mavedb:09999999-a-1").mock(
        return_value=httpx.Response(404, json={"detail": "missing"})
    )
    schemas = await _aschemas(facade)
    res = await facade.call_tool("get_score_set", {"urn": "urn:mavedb:09999999-a-1"})
    payload = structured(res)
    assert payload["success"] is False
    # The error envelope must ALSO validate against the tool's permissive schema.
    Draft202012Validator(schemas["get_score_set"]).validate(payload)


def test_every_tool_has_output_schema(facade: Any) -> None:
    schemas = _schemas(facade)
    assert set(schemas) == set(_CALLS)
