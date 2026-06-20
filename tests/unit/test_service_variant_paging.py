"""Service-level paging/token-discipline tests for get_variant_scores (GAP-D)."""

from __future__ import annotations

import httpx
import respx

from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures

BASE = fixtures.BASE_URL


@respx.mock(base_url=BASE)
async def test_get_variant_scores_ladder_only_on_first_page(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # GAP-D: the full calibration ladder is identical on every page (it is record-
    # level data), so re-shipping it per page wastes ~95% of a small page's tokens.
    # Emit it once (page 0, start=0) and drop it on forward pages, while the per-row
    # matched classification still rides on every page so the score stays readable.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    first = await service.get_variant_scores(fixtures.SCORE_SET_URN, start=0, limit=2)
    assert "calibrations" in first  # ladder ships once, on the first page
    later = await service.get_variant_scores(fixtures.SCORE_SET_URN, start=1, limit=2)
    assert "calibrations" not in later  # not re-shipped on a forward page
    assert later["rows"][0]["classification"] == "abnormal"  # per-row class still rides


@respx.mock(base_url=BASE)
async def test_get_variant_scores_ladder_kept_on_forward_page_at_full(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # full is the explicit "give me everything" mode: keep the ladder on every page.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    later = await service.get_variant_scores(
        fixtures.SCORE_SET_URN, start=1, limit=2, response_mode="full"
    )
    assert "calibrations" in later
