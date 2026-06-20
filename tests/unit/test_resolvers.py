"""Tests for the P1 resolver service methods (respx-backed)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavedb_link.exceptions import InvalidInputError, NotFoundError
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures

BASE = fixtures.BASE_URL


# --- find_variant (cross-dataset VRS lookup) -----------------------------------


async def test_find_variant_rejects_non_vrs_id(service: MaveDBService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await service.find_variant("CA000002")
    assert exc.value.field == "vrs_id"


@respx.mock(base_url=BASE)
async def test_find_variant_spans_score_sets(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/mapped-variants/vrs/{fixtures.VRS_ID_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VRS_CROSS_DATASET_RAW)
    )
    out = await service.find_variant(fixtures.VRS_ID, enrich=False)
    assert out["total"] == 2
    sets = {h["score_set_urn"] for h in out["hits"]}
    assert sets == {fixtures.SCORE_SET_URN, fixtures.SCORE_SET_URN_2}
    assert out["hits"][0]["vrs_id"] == fixtures.VRS_ID
    assert out["hits"][0]["clingen_allele_id"] == "CA000002"


@respx.mock(base_url=BASE)
async def test_find_variant_enriches_with_score_and_classification(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/mapped-variants/vrs/{fixtures.VRS_ID_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VRS_CROSS_DATASET_RAW[:1])
    )
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    out = await service.find_variant(fixtures.VRS_ID, enrich=True)
    hit = out["hits"][0]
    assert hit["score"] == -1.2
    assert hit["classifications"][0]["classification"] == "abnormal"


# --- get_hgvs_validation -------------------------------------------------------


@respx.mock(base_url=BASE)
async def test_get_hgvs_validation_valid(respx_mock: respx.Router, service: MaveDBService) -> None:
    route = respx_mock.post("/hgvs/validate").mock(return_value=httpx.Response(200, json=True))
    out = await service.get_hgvs_validation("NM_000059.4:c.8167G>A")
    assert out["valid"] is True
    assert out["variant"] == "NM_000059.4:c.8167G>A"
    body = route.calls[0].request.read().decode()
    assert "NM_000059.4:c.8167G>A" in body


@respx.mock(base_url=BASE)
async def test_get_hgvs_validation_invalid_surfaces_reason(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.post("/hgvs/validate").mock(
        return_value=httpx.Response(
            400, json={"detail": "reference (A) does not agree with reference sequence (G)"}
        )
    )
    out = await service.get_hgvs_validation("NM_000059.4:c.8167A>G")
    assert out["valid"] is False
    assert "does not agree" in out["message"]


async def test_get_hgvs_validation_rejects_empty(service: MaveDBService) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_hgvs_validation("   ")


# --- get_classified_variants ---------------------------------------------------


def _mock_calibration(respx_mock: respx.Router) -> None:
    respx_mock.get(f"/score-calibrations/score-set/{fixtures.SCORE_SET_URN}/primary").mock(
        return_value=httpx.Response(200, json=fixtures.PRIMARY_CALIBRATION_RAW)
    )
    respx_mock.get(f"/score-calibrations/{fixtures.CALIBRATION_URN}/variants").mock(
        return_value=httpx.Response(200, json=fixtures.CALIBRATION_VARIANTS_RAW)
    )


@respx.mock(base_url=BASE)
async def test_get_classified_variants_filters_abnormal(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    _mock_calibration(respx_mock)
    out = await service.get_classified_variants(fixtures.SCORE_SET_URN, classification="abnormal")
    assert out["calibration_urn"] == fixtures.CALIBRATION_URN
    assert out["total"] == 1
    v = out["variants"][0]
    assert v["variant_urn"] == f"{fixtures.SCORE_SET_URN}#2"
    assert v["classification"] == "abnormal"
    assert v["score"] == 0.94
    assert v["acmg"] == "PS3"


@respx.mock(base_url=BASE)
async def test_get_classified_variants_no_filter_returns_all(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    _mock_calibration(respx_mock)
    out = await service.get_classified_variants(fixtures.SCORE_SET_URN)
    assert out["total"] == 2


async def test_get_classified_variants_rejects_unknown_class(service: MaveDBService) -> None:
    with pytest.raises(InvalidInputError) as exc:
        await service.get_classified_variants(fixtures.SCORE_SET_URN, classification="bogus")
    assert exc.value.field == "classification"


@respx.mock(base_url=BASE)
async def test_get_classified_variants_no_calibration_is_not_found(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/score-calibrations/score-set/{fixtures.SCORE_SET_URN}/primary").mock(
        return_value=httpx.Response(404, json={"detail": "no calibration"})
    )
    with pytest.raises(NotFoundError):
        await service.get_classified_variants(fixtures.SCORE_SET_URN)
