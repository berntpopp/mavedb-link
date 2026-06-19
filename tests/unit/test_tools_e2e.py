"""End-to-end facade tests: call each tool, assert envelope + chaining behaviour."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from tests import fixtures

BASE = fixtures.BASE_URL


def _mock_all(router: respx.Router) -> None:
    """Register the full happy-path route surface."""
    router.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SETS_SEARCH_RESPONSE)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/mapped-variants").mock(
        return_value=httpx.Response(200, json=fixtures.MAPPED_VARIANTS_RAW)
    )
    router.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    router.get("/genes/UBE2I").mock(return_value=httpx.Response(200, json=fixtures.GENE_RESPONSE))
    router.get(f"/experiments/{fixtures.EXPERIMENT_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.EXPERIMENT_RAW)
    )
    router.post("/experiments/search").mock(
        return_value=httpx.Response(200, json=fixtures.EXPERIMENTS_SEARCH_RESPONSE)
    )
    router.get(f"/collections/{fixtures.COLLECTION_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.COLLECTION_RAW)
    )
    router.get("/api/version").mock(
        return_value=httpx.Response(200, json=fixtures.API_VERSION_RESPONSE)
    )


def _assert_envelope_ok(payload: dict[str, Any]) -> None:
    assert payload["success"] is True
    meta = payload["_meta"]
    assert meta["tool"]
    assert meta["request_id"]
    assert "next_commands" in meta
    assert "capabilities_version" in meta


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_server_capabilities(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_server_capabilities", {})
    payload = structured(res)
    _assert_envelope_ok(payload)
    assert payload["server"] == "mavedb-link"
    assert "tool_signatures" in payload


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_diagnostics(respx_mock: respx.Router, facade: Any, structured: Any) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_diagnostics", {})
    payload = structured(res)
    _assert_envelope_ok(payload)
    assert payload["api_reachable"] is True
    assert payload["api_version"] == "2026.2.4"
    assert "runtime" in payload and "build" in payload


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_search_score_sets_chains(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("search_score_sets", {"text": "UBE2I"})
    payload = structured(res)
    _assert_envelope_ok(payload)
    assert payload["results"][0]["urn"] == fixtures.SCORE_SET_URN
    # chains to opening the top hit
    assert payload["_meta"]["next_commands"][0]["tool"] == "get_score_set"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_score_set_chains_to_scores(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_score_set", {"urn": fixtures.SCORE_SET_URN})
    payload = structured(res)
    _assert_envelope_ok(payload)
    tools = [s["tool"] for s in payload["_meta"]["next_commands"]]
    assert "get_variant_scores" in tools


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_variant_scores(respx_mock: respx.Router, facade: Any, structured: Any) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_variant_scores", {"urn": fixtures.SCORE_SET_URN, "limit": 3})
    payload = structured(res)
    _assert_envelope_ok(payload)
    assert payload["returned"] == 3
    assert payload["rows"][0]["score"] == 0.5


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_variant_score_by_urn_chains(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_variant_score", {"urn": fixtures.VARIANT_URN})
    payload = structured(res)
    _assert_envelope_ok(payload)
    assert payload["score"] == -1.2
    assert payload["score_set_urn"] == fixtures.SCORE_SET_URN
    tools = [s["tool"] for s in payload["_meta"]["next_commands"]]
    assert "get_score_set" in tools


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_variant_score_by_hgvs(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool(
        "get_variant_score", {"urn": fixtures.SCORE_SET_URN, "hgvs": "c.2T>G"}
    )
    payload = structured(res)
    _assert_envelope_ok(payload)
    assert payload["match_count"] == 1
    assert payload["matches"][0]["score"] == -1.2


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_gene_score_sets(respx_mock: respx.Router, facade: Any, structured: Any) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_gene_score_sets", {"symbol": "UBE2I"})
    payload = structured(res)
    _assert_envelope_ok(payload)
    assert payload["gene"]["symbol"] == "UBE2I"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_experiment_and_search(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_experiment", {"urn": fixtures.EXPERIMENT_URN})
    assert structured(res)["score_set_urns"] == [fixtures.SCORE_SET_URN]
    res2 = await facade.call_tool("search_experiments", {"text": "UBE2I"})
    assert structured(res2)["results"][0]["urn"] == fixtures.EXPERIMENT_URN


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_mapped_variants_and_collection(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_mapped_variants", {"urn": fixtures.SCORE_SET_URN, "limit": 1})
    payload = structured(res)
    assert payload["truncated"] is True
    res2 = await facade.call_tool("get_collection", {"urn": fixtures.COLLECTION_URN})
    assert structured(res2)["name"] == "UBE2I datasets"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_meta_tiering_by_response_mode(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    minimal = structured(
        await facade.call_tool(
            "get_score_set", {"urn": fixtures.SCORE_SET_URN, "response_mode": "minimal"}
        )
    )
    assert set(minimal["_meta"].keys()) == {"tool", "request_id"}

    compact = structured(
        await facade.call_tool(
            "get_score_set", {"urn": fixtures.SCORE_SET_URN, "response_mode": "compact"}
        )
    )
    assert "next_commands" in compact["_meta"]
    assert "elapsed_ms" not in compact["_meta"]

    full = structured(
        await facade.call_tool(
            "get_score_set", {"urn": fixtures.SCORE_SET_URN, "response_mode": "full"}
        )
    )
    assert "elapsed_ms" in full["_meta"]


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_alias_rewrite_disclosed(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    # 'query' is an alias for 'text' on search_score_sets
    res = await facade.call_tool("search_score_sets", {"query": "UBE2I"})
    payload = structured(res)
    assert payload["success"] is True
    assert ["query", "text"] in payload["_meta"]["argument_aliases_applied"]


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_offset_alias_rewrites_to_start_on_variant_scores(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    # DEF-7: `offset` is accepted (and disclosed) as an alias for `start`.
    res = await facade.call_tool(
        "get_variant_scores", {"urn": fixtures.SCORE_SET_URN, "offset": 0, "limit": 3}
    )
    payload = structured(res)
    assert payload["success"] is True
    assert ["offset", "start"] in payload["_meta"]["argument_aliases_applied"]
    assert payload["total"] == 12720


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_not_found_returns_structured_error(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    respx_mock.get("/score-sets/urn:mavedb:09999999-a-1").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    res = await facade.call_tool("get_score_set", {"urn": "urn:mavedb:09999999-a-1"})
    payload = structured(res)
    assert payload["success"] is False
    assert payload["error_code"] == "not_found"
    assert payload["recovery_action"] == "reformulate_input"
    assert payload["_meta"]["next_commands"]


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_unknown_argument_returns_invalid_input(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_score_set", {"urn": fixtures.SCORE_SET_URN, "bogus": 1})
    payload = structured(res)
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"
    assert "allowed_values" in payload


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_score_set_experiment_urn_is_invalid_input(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    # DEF-4: wrong-granularity URN → invalid_input/field, not a misleading not_found.
    res = await facade.call_tool("get_score_set", {"urn": "urn:mavedb:00000001-a"})
    payload = structured(res)
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"
    assert payload["field"] == "urn"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_invalid_score_set_urn_is_invalid_input(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    _mock_all(respx_mock)
    res = await facade.call_tool("get_variant_scores", {"urn": "urn:mavedb:00000001-a"})
    payload = structured(res)
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"


def test_json_serialisable_payloads_helper() -> None:
    # Guards the fixtures stay JSON-round-trippable (used by respx json=).
    assert json.loads(json.dumps(fixtures.SCORE_SET_RAW))["urn"] == fixtures.SCORE_SET_URN
