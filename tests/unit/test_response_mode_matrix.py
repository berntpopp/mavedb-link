"""Phase 0.1 exit gate: every tool succeeds at all four response_mode tiers.

Runs the FULL facade stack against a mocked surface that deliberately includes the
GAP-2 offending shape (a variant record whose score is a STRING) and a calibrated
score set, so the tier matrix locks the class of bug where richer enrichment used
to crash where compact succeeded. Also asserts the uniform observability contract
(elapsed_ms + truncated + token_estimate on every response).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from mavedb_link.services.shaping import RESPONSE_MODES
from tests import fixtures

#: (tool, args) for every tool that accepts response_mode (13 of 15).
_MODED_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("search_score_sets", {"text": "UBE2I"}),
    ("get_score_set", {"urn": fixtures.SCORE_SET_URN}),
    ("get_variant_scores", {"urn": fixtures.SCORE_SET_URN}),
    ("get_variant_score", {"urn": fixtures.VARIANT_URN}),  # by-urn (string-score record)
    ("get_variant_score", {"urn": fixtures.SCORE_SET_URN, "hgvs": "c.2T>G"}),  # by-hgvs
    ("get_gene_score_sets", {"symbol": "UBE2I"}),
    ("get_experiment", {"urn": fixtures.EXPERIMENT_URN}),
    ("search_experiments", {"text": "UBE2I"}),
    ("get_mapped_variants", {"urn": fixtures.SCORE_SET_URN}),
    ("get_collection", {"urn": fixtures.COLLECTION_URN}),
    ("find_variant", {"vrs_id": fixtures.VRS_ID}),
    ("get_hgvs_validation", {"variant": "NM_000059.4:c.8167G>A"}),
    ("get_classified_variants", {"urn": fixtures.SCORE_SET_URN}),
    ("get_score_distribution", {"urn": fixtures.SCORE_SET_URN, "score": 0.5}),
]

_DISCOVERY_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("get_server_capabilities", {}),
    ("get_diagnostics", {}),
]


def _mock_surface(router: respx.Router) -> None:
    """Mock every upstream route, using the GAP-2 string-score variant record."""
    router.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SETS_SEARCH_RESPONSE)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/mapped-variants").mock(
        return_value=httpx.Response(200, json=fixtures.MAPPED_VARIANTS_RAW)
    )
    # The offending shape: a variant record whose score is a string.
    router.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW_STR_SCORE)
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
    router.get(f"/mapped-variants/vrs/{fixtures.VRS_ID_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VRS_CROSS_DATASET_RAW[:1])
    )
    router.post("/hgvs/validate").mock(return_value=httpx.Response(200, json=True))
    router.get(f"/score-calibrations/score-set/{fixtures.SCORE_SET_URN}/primary").mock(
        return_value=httpx.Response(200, json=fixtures.PRIMARY_CALIBRATION_RAW)
    )
    router.get(f"/score-calibrations/{fixtures.CALIBRATION_URN}/variants").mock(
        return_value=httpx.Response(200, json=fixtures.CALIBRATION_VARIANTS_RAW)
    )


def _assert_observability(payload: dict[str, Any]) -> None:
    meta = payload["_meta"]
    assert "elapsed_ms" in meta
    assert isinstance(meta["truncated"], bool)
    assert isinstance(meta["token_estimate"], int)


@pytest.mark.parametrize("tool,args", _MODED_TOOLS)
@pytest.mark.parametrize("mode", RESPONSE_MODES)
async def test_tier_matrix_every_tool_every_mode_succeeds(
    respx_router: respx.Router,
    facade: Any,
    structured: Any,
    tool: str,
    args: dict[str, Any],
    mode: str,
) -> None:
    _mock_surface(respx_router)
    res = await facade.call_tool(tool, {**args, "response_mode": mode})
    payload = structured(res)
    assert payload["success"] is True, f"{tool}@{mode}: {payload.get('message')}"
    _assert_observability(payload)


@pytest.mark.parametrize("tool,args", _DISCOVERY_TOOLS)
async def test_discovery_tools_succeed(
    respx_router: respx.Router, facade: Any, structured: Any, tool: str, args: dict[str, Any]
) -> None:
    _mock_surface(respx_router)
    payload = structured(await facade.call_tool(tool, args))
    assert payload["success"] is True
    _assert_observability(payload)


async def test_calibrated_listing_is_leaner_than_record(
    respx_router: respx.Router, facade: Any, structured: Any
) -> None:
    # The headline token fix: listing a calibrated gene must NOT inline the per-bin
    # calibration ladder. The gene listing stays far leaner than the record that does
    # carry it, and surfaces has_calibrations as the drill-in signal.
    calibrated = fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW
    respx_router.get("/genes/BRCA1").mock(
        return_value=httpx.Response(
            200, json={**fixtures.GENE_RESPONSE, "symbol": "BRCA1", "scoreSets": [calibrated]}
        )
    )
    respx_router.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"scoreSets": [], "numScoreSets": 0})
    )
    respx_router.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=calibrated)
    )

    listing = structured(await facade.call_tool("get_gene_score_sets", {"symbol": "BRCA1"}))
    record = structured(await facade.call_tool("get_score_set", {"urn": fixtures.SCORE_SET_URN}))

    assert listing["success"] and record["success"]
    entry = listing["score_sets"][0]
    assert "score_calibrations" not in entry
    assert entry["has_calibrations"] is True
    assert record["score_calibrations"]  # the record carries the ladder the listing dropped
    assert listing["_meta"]["token_estimate"] < record["_meta"]["token_estimate"]


async def test_search_listing_suppresses_calibrations(
    respx_router: respx.Router, facade: Any, structured: Any
) -> None:
    # The same token discipline through search_score_sets: a calibrated hit shows the
    # presence flag, not the inlined ladder.
    respx_router.post("/score-sets/search").mock(
        return_value=httpx.Response(
            200,
            json={"scoreSets": [fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW], "numScoreSets": 1},
        )
    )
    out = structured(await facade.call_tool("search_score_sets", {"text": "BRCA1"}))
    assert out["success"]
    entry = out["results"][0]
    assert "score_calibrations" not in entry
    assert entry["has_calibrations"] is True


async def test_string_score_variant_classifies_across_tiers(
    respx_router: respx.Router, facade: Any, structured: Any
) -> None:
    # The exact GAP-2 reproduction at the facade boundary: standard/full used to
    # return an opaque internal_error; now they classify the coerced score.
    _mock_surface(respx_router)
    for mode in ("compact", "standard", "full"):
        payload = structured(
            await facade.call_tool(
                "get_variant_score", {"urn": fixtures.VARIANT_URN, "response_mode": mode}
            )
        )
        assert payload["success"] is True, mode
        v = payload["variants"][0]
        assert v["score"] == -1.2, mode
        assert v["classifications"][0]["classification"] == "abnormal", mode
