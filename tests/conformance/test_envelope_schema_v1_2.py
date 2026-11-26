"""Response-Envelope v1.2: every data tool's result validates against the published schema.

The schema is vendored from berntpopp/genefoundry-envelope, tag v1.2.0
(``envelope-v1.2.schema.json``); its digest is pinned in the sibling ``.sha256`` file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from jsonschema import Draft202012Validator

from mavedb_link.mcp.capabilities import TOOLS
from tests import fixtures
from tests.unit.test_tools_e2e import _mock_all

_HERE = Path(__file__).parent
_SCHEMA_FILE = _HERE / "envelope-v1.2.schema.json"
_VALIDATOR = Draft202012Validator(json.loads(_SCHEMA_FILE.read_text(encoding="utf-8")))

_OPS_TOOLS = {"get_server_capabilities", "get_diagnostics"}  # outside the v1.2 rule
_PROVENANCE = ("source", "data_version", "retrieved_at")

#: One successful call per data tool, served by the mocked upstream.
_CALLS: dict[str, dict[str, Any]] = {
    "search_score_sets": {"text": "UBE2I"},
    "get_score_set": {"urn": fixtures.SCORE_SET_URN},
    "get_variant_scores": {"urn": fixtures.SCORE_SET_URN, "limit": 3},
    "get_variant_score": {"urn": fixtures.VARIANT_URN},
    "get_gene_score_sets": {"gene_symbol": "UBE2I"},
    "get_experiment": {"urn": fixtures.EXPERIMENT_URN},
    "search_experiments": {"text": "UBE2I"},
    "get_mapped_variants": {"urn": fixtures.SCORE_SET_URN, "limit": 1},
    "get_collection": {"urn": fixtures.COLLECTION_URN},
    "find_variant": {"vrs_id": fixtures.VRS_ID},
    "get_hgvs_validation": {"variant": "NM_000059.4:c.8167G>A"},
    "get_classified_variants": {"urn": fixtures.SCORE_SET_URN, "classification": "abnormal"},
    "get_score_distribution": {"urn": fixtures.SCORE_SET_URN, "score": 0.5},
}


def _violations(envelope: dict[str, Any]) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
        for e in _VALIDATOR.iter_errors(envelope)
    ]


def test_vendored_schema_is_the_pinned_release() -> None:
    pin = (_HERE / "envelope-v1.2.schema.sha256").read_text(encoding="utf-8").strip()
    assert hashlib.sha256(_SCHEMA_FILE.read_bytes()).hexdigest() == pin


def test_every_data_tool_is_covered() -> None:
    assert set(_CALLS) == set(TOOLS) - _OPS_TOOLS


@pytest.mark.parametrize("mode", ["minimal", "compact", "full"])
@pytest.mark.parametrize("tool", sorted(_CALLS))
@respx.mock(base_url=fixtures.BASE_URL, assert_all_called=False)
async def test_data_tool_result_validates(
    respx_mock: respx.Router, facade: Any, structured: Any, tool: str, mode: str
) -> None:
    _mock_all(respx_mock)
    result = await facade.call_tool(tool, {**_CALLS[tool], "response_mode": mode})
    envelope = structured(result)
    assert envelope["success"] is True
    assert _violations(envelope) == []
    mirrored = json.loads(result.content[0].text)["_meta"]  # the text block mirrors _meta
    for field in _PROVENANCE:
        assert isinstance(envelope["_meta"][field], str) and envelope["_meta"][field], field
        assert mirrored[field] == envelope["_meta"][field]


@respx.mock(base_url=fixtures.BASE_URL, assert_all_called=False)
async def test_error_results_validate(respx_mock: respx.Router, facade: Any) -> None:
    respx_mock.get("/score-sets/urn:mavedb:09999999-a-1").mock(return_value=httpx.Response(404))
    calls = [
        ("get_score_set", {"urn": "urn:mavedb:09999999-a-1"}),  # not_found
        ("get_score_set", {"urn": fixtures.SCORE_SET_URN, "bogus": 1}),  # argument binding
        ("get_score_set", {"urn": "urn:mavedb:00000001-a"}),  # invalid_input
    ]
    for tool, arguments in calls:
        result = await facade.call_tool(tool, arguments)
        assert result.structured_content["success"] is False
        assert list(_VALIDATOR.iter_errors(result.structured_content)) == []


def test_unknown_tool_error_validates() -> None:
    from mavedb_link.mcp.notfound_guard import unknown_tool_envelope

    assert list(_VALIDATOR.iter_errors(unknown_tool_envelope())) == []
