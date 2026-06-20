"""Tests for get_score_distribution (server-side stats over the scores CSV)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavedb_link.exceptions import NotFoundError
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures

BASE = fixtures.BASE_URL


@respx.mock(base_url=BASE)
async def test_score_distribution_summary(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.DISTRIBUTION_SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_score_distribution(fixtures.SCORE_SET_URN)
    assert out["n"] == 10
    assert out["min"] == 0.0
    assert out["max"] == 9.0
    assert out["median"] == 4.5
    assert len(out["histogram"]) == 10
    assert sum(b["count"] for b in out["histogram"]) == 10


@respx.mock(base_url=BASE)
async def test_score_distribution_query_percentile_and_classification(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.DISTRIBUTION_SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    out = await service.get_score_distribution(fixtures.SCORE_SET_URN, score=0.94)
    assert out["query"]["score"] == 0.94
    assert out["query"]["percentile"] == 10.0  # one of ten scores is below 0.94
    # GAP-1: the matched band travels with the query at every tier...
    assert out["query"]["classifications"][0]["classification"] == "abnormal"
    # ...but the full threshold ladder is gated to full (not duplicated at compact).
    assert "calibrations" not in out


@respx.mock(base_url=BASE)
async def test_score_distribution_ladder_gated_to_full(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.DISTRIBUTION_SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    compact = await service.get_score_distribution(fixtures.SCORE_SET_URN, score=0.94)
    assert "calibrations" not in compact
    full = await service.get_score_distribution(
        fixtures.SCORE_SET_URN, score=0.94, response_mode="full"
    )
    assert full["calibrations"][0]["title"] == "IGVF Controls"


@respx.mock(base_url=BASE)
async def test_score_distribution_no_scores_is_not_found(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text="accession,hgvs_nt,score\n")
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    with pytest.raises(NotFoundError):
        await service.get_score_distribution(fixtures.SCORE_SET_URN)


async def test_score_distribution_rejects_non_score_set_urn(service: MaveDBService) -> None:
    from mavedb_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        await service.get_score_distribution("urn:mavedb:00000001-a")
