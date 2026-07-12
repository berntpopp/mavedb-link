"""Tests for the unified single-variant lookup (services/variant_lookup.py).

Both resolution paths -- a full variant URN and a score-set URN + hgvs -- return
the SAME top-level shape and per-variant key set (F2); embedded mapped_variants
are opt-in (standard/full) and current-only unless full.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from mavedb_link.exceptions import InvalidInputError, NotFoundError
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures

BASE = fixtures.BASE_URL


@respx.mock(base_url=BASE)
async def test_get_variant_score_by_variant_urn(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # DEF-6: a full variant URN resolves directly via GET /variants/{urn} (the '#'
    # index is percent-encoded in the request path).
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    # The calibration enrichment reads the (uncalibrated) score-set record.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_variant_score(fixtures.VARIANT_URN)
    assert out["urn"] == fixtures.SCORE_SET_URN
    assert out["resolved_by"] == "variant_urn"
    assert out["match_count"] == 1
    v = out["variants"][0]
    assert v["variant_urn"] == fixtures.VARIANT_URN
    assert v["variant_index"] == 2
    assert v["score_set_urn"] == fixtures.SCORE_SET_URN
    assert v["score"] == -1.2
    assert v["hgvs_nt"] == "c.2T>G"
    assert "classifications" not in v  # uncalibrated set -> no classification
    assert "mapped_variants" not in v  # compact carries no mapped_variants (F2)


@respx.mock(base_url=BASE)
async def test_get_variant_score_by_hgvs_filters_table(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # DEF-6: a score-set URN + hgvs scans the table and returns the matching row.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs="c.2T>G")
    assert out["urn"] == fixtures.SCORE_SET_URN
    assert out["resolved_by"] == "hgvs"
    assert out["query"] == "c.2T>G"
    assert out["match_count"] == 1
    assert out["variants"][0]["score"] == -1.2
    assert out["variants"][0]["variant_urn"] == fixtures.VARIANT_URN


@respx.mock(base_url=BASE)
async def test_get_variant_score_by_urn_attaches_classifications(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # The naked score (-1.2) becomes interpretable: abnormal under both calibrations.
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    out = await service.get_variant_score(fixtures.VARIANT_URN)
    v = out["variants"][0]
    assert v["score"] == -1.2
    cls = v["classifications"]
    assert cls[0]["calibration"] == "IGVF Controls"
    assert cls[0]["classification"] == "abnormal"
    assert cls[0]["acmg"] == "PS3"
    assert cls[1]["calibration"] == "ExCALIBR calibration"
    assert cls[1]["classification"] == "abnormal"


@respx.mock(base_url=BASE)
async def test_get_variant_score_classification_is_best_effort(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # If the calibration fetch fails, the score still returns (no classifications).
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(503, text="down")
    )
    out = await service.get_variant_score(fixtures.VARIANT_URN)
    assert out["variants"][0]["score"] == -1.2
    assert "classifications" not in out["variants"][0]


@respx.mock(base_url=BASE)
async def test_get_variant_score_by_hgvs_attaches_classifications(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    out = await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs="c.2T>G")
    assert out["variants"][0]["score"] == -1.2
    # GAP-1: the per-variant matched band is the interpretation at compact; the
    # full threshold ladder is NOT duplicated top-level.
    assert out["variants"][0]["classifications"][0]["classification"] == "abnormal"
    assert "calibrations" not in out


@respx.mock(base_url=BASE)
async def test_get_variant_score_full_ladder_gated_to_full(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # GAP-1: the heavy top-level threshold ladder appears ONLY at full; compact and
    # standard rely on the inline matched-band classification.
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    for mode in ("compact", "standard"):
        out = await service.get_variant_score(fixtures.VARIANT_URN, response_mode=mode)
        assert "calibrations" not in out, mode
        assert out["variants"][0]["classifications"]  # matched band still present
    full = await service.get_variant_score(fixtures.VARIANT_URN, response_mode="full")
    assert full["calibrations"][0]["title"] == "IGVF Controls"


@respx.mock(base_url=BASE)
async def test_get_variant_score_hgvs_not_found(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    with pytest.raises(NotFoundError):
        await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs="c.999Z>Q")


@respx.mock(base_url=BASE)
async def test_get_variant_score_both_paths_identical_key_sets(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # F2: resolving the SAME variant by URN and by hgvs returns the same top-level
    # and per-variant key sets (the two used to diverge into {score} vs {matches}).
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    by_urn = await service.get_variant_score(fixtures.VARIANT_URN)
    by_hgvs = await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs="c.2T>G")
    assert set(by_urn) == set(by_hgvs)
    assert set(by_urn["variants"][0]) == set(by_hgvs["variants"][0])


@respx.mock(base_url=BASE)
async def test_get_variant_score_standard_mapped_variants_current_only(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # F2: at standard, the by-URN view carries current-only mapped_variants; at full
    # it keeps the superseded rows.
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW_WITH_HISTORY)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    standard = await service.get_variant_score(fixtures.VARIANT_URN, response_mode="standard")
    assert [m["current"] for m in standard["variants"][0]["mapped_variants"]] == [True]
    full = await service.get_variant_score(fixtures.VARIANT_URN, response_mode="full")
    assert {m["current"] for m in full["variants"][0]["mapped_variants"]} == {True, False}


@respx.mock(base_url=BASE)
async def test_get_variant_score_by_hgvs_standard_fetches_record_for_mapped(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # F2: at standard the hgvs path resolves the matched accession's record so the
    # view (mapped_variants) is identical to the by-URN path.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_variant_score(
        fixtures.SCORE_SET_URN, hgvs="c.2T>G", response_mode="standard"
    )
    assert out["variants"][0]["mapped_variants"][0]["clingen_allele_id"] == "CA000002"


@respx.mock(base_url=BASE)
async def test_get_variant_score_bare_hgvs_resolves_accession_prefixed_row(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # F5: a bare 'c.8168A>G' resolves a stored 'ENST...:c.8168A>G' (used to 404).
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.PREFIXED_SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs="c.8168A>G")
    assert out["match_count"] == 1
    assert out["variants"][0]["score"] == 0.94
    assert out["variants"][0]["hgvs_nt"] == "ENST00000380152.8:c.8168A>G"


@respx.mock(base_url=BASE)
async def test_get_variant_score_string_score_classifies_at_standard(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # GAP-2 regression: a variant record whose score is a STRING used to crash the
    # standard/full path (classify_score: str <= float). It must now coerce and
    # classify, returning the correct class at every tier.
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW_STR_SCORE)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    for mode in ("compact", "standard", "full"):
        out = await service.get_variant_score(fixtures.VARIANT_URN, response_mode=mode)
        v = out["variants"][0]
        assert v["score"] == -1.2, mode  # coerced to float in the output
        assert v["classifications"][0]["classification"] == "abnormal", mode


@respx.mock(base_url=BASE)
async def test_get_variant_score_by_hgvs_string_score_classifies_at_full(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # The by-hgvs path at full fetches each matched record (string score) — the
    # exact path reproduced live on urn:mavedb:00001242-a-1.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW_STR_SCORE)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    out = await service.get_variant_score(
        fixtures.SCORE_SET_URN, hgvs="c.2T>G", response_mode="full"
    )
    assert out["variants"][0]["score"] == -1.2
    assert out["variants"][0]["classifications"][0]["classification"] == "abnormal"


async def test_get_variant_score_requires_hgvs_for_score_set(service: MaveDBService) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_variant_score(fixtures.SCORE_SET_URN)


async def test_get_variant_score_rejects_experiment_urn(service: MaveDBService) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_variant_score("urn:mavedb:00000001-a", hgvs="c.2T>G")


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_get_variant_score_rejects_whitespace_padded_oversize_hgvs_before_upstream(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # F-09 gate3 (Codex re-gate): get_variant_score(hgvs=) must validate the RAW,
    # UN-STRIPPED hgvs FIRST -- the raw-length bound applies to the exact string the caller
    # sent, BEFORE any strip/normalize or the upstream score-table scan. A valid short core
    # padded with thousands of leading spaces has a RAW length over the bound yet STRIPS
    # back to a valid core, so a caller that stripped before validating would accept and
    # forward it. The padding is present in what the caller passes, so a strip-then-validate
    # regression would let it reach upstream and this test (route.call_count == 0) fails.
    from mavedb_link.constants import MAX_HGVS_VARIANT_CHARS
    from mavedb_link.identifiers import validate_hgvs_variant

    core = "c.2T>G"
    route = respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    padded = " " * (MAX_HGVS_VARIANT_CHARS + 1000) + core
    assert len(padded) > MAX_HGVS_VARIANT_CHARS
    # Positive control: the un-padded core validates fine and returns normalized -- so ONLY
    # the raw (pre-strip) length makes `padded` illegal, and a strip-first caller would
    # recover exactly that accepted core and forward it upstream (the regression).
    assert validate_hgvs_variant(core) == core
    assert padded.strip() == core
    with pytest.raises(InvalidInputError) as exc:
        await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs=padded)
    assert exc.value.field == "variant"
    assert route.call_count == 0  # never forwarded upstream (no score-table scan / cache read)
    # The fixed error must not echo the caller's (stripped) payload.
    assert core not in str(exc.value)
