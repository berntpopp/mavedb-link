"""Tests for MaveDBService (respx-backed: client -> service -> shaping)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavedb_link.exceptions import InvalidInputError
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures

BASE = fixtures.BASE_URL


@respx.mock(base_url=BASE)
async def test_search_score_sets(respx_mock: respx.Router, service: MaveDBService) -> None:
    route = respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SETS_SEARCH_RESPONSE)
    )
    out = await service.search_score_sets("UBE2I", limit=10)
    assert out["query"] == "UBE2I"
    assert out["total"] == 1
    assert out["returned"] == 1
    assert out["results"][0]["urn"] == fixtures.SCORE_SET_URN
    body = route.calls[0].request.read().decode()
    assert "UBE2I" in body
    assert '"published": true' in body or '"published":true' in body


@respx.mock(base_url=BASE)
async def test_search_score_sets_clamps_limit(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    route = respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"scoreSets": [], "numScoreSets": 0})
    )
    out = await service.search_score_sets("x", limit=10_000)
    # limit clamped to MAX_SEARCH_LIMIT (100)
    assert out["limit"] == 100
    assert route.called


@respx.mock(base_url=BASE)
async def test_search_reranks_target_gene_above_namesake(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # DEF-2: BAP1 ("BRCA1-Associated…") comes first upstream; the BRCA1-target set
    # must rank above it for text="BRCA1".
    bap1 = {
        "urn": "urn:mavedb:00000662-0-1",
        "title": "BAP1 SGE",
        "targetGenes": [{"name": "BAP1", "category": "protein_coding"}],
    }
    brca1 = {
        "urn": "urn:mavedb:00000081-a-1",
        "title": "BRCA1 functional",
        "targetGenes": [{"name": "BRCA1", "category": "protein_coding"}],
    }
    respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"scoreSets": [bap1, brca1], "numScoreSets": 2})
    )
    out = await service.search_score_sets("BRCA1")
    assert out["results"][0]["urn"] == "urn:mavedb:00000081-a-1"


@respx.mock(base_url=BASE)
async def test_search_organism_facet_null_inclusive_reports_excluded(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # DEF-3: a human-organism filter must KEEP unknown-organism sets and report
    # the count of records it actually dropped.
    def _ss(urn: str, organism: str | None) -> dict:
        taxonomy = {"organismName": organism} if organism is not None else {}
        return {
            "urn": urn,
            "title": "BRCA2",
            "targetGenes": [
                {
                    "name": "BRCA2",
                    "category": "protein_coding",
                    "targetSequence": {"taxonomy": taxonomy},
                }
            ],
        }

    items = [
        _ss("urn:mavedb:00001224-a-1", "Homo sapiens"),
        _ss("urn:mavedb:00001268-a-1", None),
        _ss("urn:mavedb:00000999-a-1", "Saccharomyces cerevisiae"),
    ]
    route = respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"scoreSets": items, "numScoreSets": 3})
    )
    out = await service.search_score_sets("BRCA2", target_organism_names=["Homo sapiens"])
    urns = {r["urn"] for r in out["results"]}
    assert "urn:mavedb:00001224-a-1" in urns
    assert "urn:mavedb:00001268-a-1" in urns  # null-inclusive
    assert "urn:mavedb:00000999-a-1" not in urns
    assert out["total"] == 2
    assert out["_meta"]["facet_excluded"]["target_organism_names"] == 1
    # organism faceting is client-side: NOT forwarded upstream
    body = route.calls[0].request.read().decode()
    assert "targetOrganismNames" not in body


@respx.mock(base_url=BASE)
async def test_get_score_set(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_score_set(fixtures.SCORE_SET_URN, response_mode="full")
    assert out["urn"] == fixtures.SCORE_SET_URN
    assert out["method_text"]


async def test_get_score_set_rejects_experiment_urn(service: MaveDBService) -> None:
    # DEF-4: an experiment URN is invalid_input (field=urn), not a misleading 404.
    with pytest.raises(InvalidInputError) as exc:
        await service.get_score_set("urn:mavedb:00000001-a")
    assert exc.value.field == "urn"


@respx.mock(base_url=BASE)
async def test_get_variant_scores(respx_mock: respx.Router, service: MaveDBService) -> None:
    route = respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_variant_scores(fixtures.SCORE_SET_URN, start=0, limit=3)
    assert out["urn"] == fixtures.SCORE_SET_URN
    assert out["returned"] == 3
    assert out["rows"][0]["score"] == 0.5
    assert route.calls[0].request.url.params["start"] == "0"
    # DEF-7: a real total (numVariants), not null; offset mirrors start
    assert out["total"] == 12720
    assert out["offset"] == 0
    assert out["next_offset"] == out["next_start"]


@respx.mock(base_url=BASE)
async def test_get_variant_scores_total_degrades_when_record_missing(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # If the score-set record fetch fails, scores still return (total falls back to None).
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(503, text="down")
    )
    out = await service.get_variant_scores(fixtures.SCORE_SET_URN, start=0, limit=3)
    assert out["returned"] == 3
    assert out["total"] is None


@respx.mock(base_url=BASE)
async def test_get_variant_scores_classifies_rows(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # Every row gets the primary-calibration verdict; the thresholds block travels
    # with the page so the score column is interpretable in one call.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    out = await service.get_variant_scores(fixtures.SCORE_SET_URN, start=0, limit=3)
    assert out["rows"][0]["classification"] == "abnormal"  # score 0.5 < 1.49
    assert out["rows"][1]["classification"] == "abnormal"  # score -1.2
    assert "classification" not in out["rows"][2]  # score is None
    assert out["calibrations"][0]["title"] == "IGVF Controls"


async def test_get_variant_scores_rejects_non_score_set_urn(service: MaveDBService) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_variant_scores("urn:mavedb:00000001-a")


@respx.mock(base_url=BASE)
async def test_get_gene_score_sets(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.get("/genes/UBE2I").mock(
        return_value=httpx.Response(200, json=fixtures.GENE_RESPONSE)
    )
    respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"scoreSets": [], "numScoreSets": 0})
    )
    out = await service.get_gene_score_sets("UBE2I")
    assert out["gene"]["symbol"] == "UBE2I"
    assert out["total"] == 1
    assert out["total_scored_variants"] == 12720
    assert out["score_sets"][0]["urn"] == fixtures.SCORE_SET_URN


@respx.mock(base_url=BASE)
async def test_get_gene_score_sets_unions_target_search(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # DEF-1: the gene endpoint and the target-name search return DIFFERENT sets;
    # the tool must return their union (deduped by URN), never one partial view.
    respx_mock.get("/genes/UBE2I").mock(
        return_value=httpx.Response(200, json=fixtures.GENE_RESPONSE)
    )
    route = respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json=fixtures.GENE_TARGET_SEARCH_RESPONSE)
    )
    out = await service.get_gene_score_sets("UBE2I")
    urns = {s["urn"] for s in out["score_sets"]}
    # union of gene-endpoint {00000001-a-1} and target-search {00000002-a-1}
    assert urns == {fixtures.SCORE_SET_URN, fixtures.SCORE_SET_URN_2}
    assert out["total"] == 2
    # differential invariant: target-search hits ⊆ returned set
    assert fixtures.SCORE_SET_URN_2 in urns
    assert out["coverage"]["union"] == 2
    assert out["coverage"]["gene_endpoint"] == 1
    assert out["coverage"]["target_search"] == 1
    # the target search was queried with targets=[symbol]
    body = route.calls[0].request.read().decode()
    assert "UBE2I" in body and "targets" in body


@respx.mock(base_url=BASE)
async def test_get_gene_score_sets_degrades_when_target_search_fails(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # The target-search augmentation is best-effort: a 5xx there must NOT fail the
    # whole gene lookup — fall back to gene-only and flag degraded coverage.
    respx_mock.get("/genes/UBE2I").mock(
        return_value=httpx.Response(200, json=fixtures.GENE_RESPONSE)
    )
    respx_mock.post("/score-sets/search").mock(return_value=httpx.Response(503, text="down"))
    out = await service.get_gene_score_sets("UBE2I")
    assert {s["urn"] for s in out["score_sets"]} == {fixtures.SCORE_SET_URN}
    assert out["coverage"]["degraded"] is True


@respx.mock(base_url=BASE)
async def test_get_experiment(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.get(f"/experiments/{fixtures.EXPERIMENT_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.EXPERIMENT_RAW)
    )
    out = await service.get_experiment(fixtures.EXPERIMENT_URN)
    assert out["urn"] == fixtures.EXPERIMENT_URN
    assert out["score_set_urns"] == [fixtures.SCORE_SET_URN]


@respx.mock(base_url=BASE)
async def test_search_experiments(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.post("/experiments/search").mock(
        return_value=httpx.Response(200, json=fixtures.EXPERIMENTS_SEARCH_RESPONSE)
    )
    out = await service.search_experiments("UBE2I")
    assert out["total"] == 1
    assert out["results"][0]["urn"] == fixtures.EXPERIMENT_URN


@respx.mock(base_url=BASE)
async def test_get_mapped_variants_pages(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/mapped-variants").mock(
        return_value=httpx.Response(200, json=fixtures.MAPPED_VARIANTS_RAW)
    )
    out = await service.get_mapped_variants(fixtures.SCORE_SET_URN, limit=1, offset=0)
    assert out["total"] == 2
    assert out["returned"] == 1
    assert out["truncated"] is True
    assert out["next_offset"] == 1
    assert out["mapped_variants"][0]["vrs_id"] == "ga4gh:VA.KJ_post1"


@respx.mock(base_url=BASE)
async def test_search_experiments_targets_derives_and_groups(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # DEF-8: a target facet on experiments is derived from the score-set target
    # search and grouped by parent experiment URN (the upstream facet is useless).
    score_sets = [
        {
            "urn": "urn:mavedb:00000081-a-1",
            "experiment": {"urn": "urn:mavedb:00000081-a"},
            "targetGenes": [{"name": "BRCA1"}],
        },
        {
            "urn": "urn:mavedb:00000081-a-2",
            "experiment": {"urn": "urn:mavedb:00000081-a"},
            "targetGenes": [{"name": "BRCA1"}],
        },
        {
            "urn": "urn:mavedb:00001237-a-1",
            "experiment": {"urn": "urn:mavedb:00001237-a"},
            "targetGenes": [{"name": "BRCA1"}],
        },
    ]
    route = respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"scoreSets": score_sets, "numScoreSets": 3})
    )
    out = await service.search_experiments(targets=["BRCA1"])
    assert [r["urn"] for r in out["results"]] == [
        "urn:mavedb:00000081-a",
        "urn:mavedb:00001237-a",
    ]
    assert out["total"] == 2
    assert out["results"][0]["num_matching_score_sets"] == 2
    # it queried the SCORE-SET search with the target facet
    body = route.calls[0].request.read().decode()
    assert "BRCA1" in body and "targets" in body


@respx.mock(base_url=BASE)
async def test_get_mapped_variants_current_only_collapses_and_orders(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # DEF-5: 2 rows/variant (current + non-current) collapse to 1/variant by default,
    # ordered numerically by variant_index so the page aligns with the scores table.
    raw = [
        {
            "variantUrn": f"{fixtures.SCORE_SET_URN}#2",
            "postMapped": {"id": "v2cur"},
            "current": True,
        },
        {
            "variantUrn": f"{fixtures.SCORE_SET_URN}#2",
            "postMapped": {"id": "v2old"},
            "current": False,
        },
        {
            "variantUrn": f"{fixtures.SCORE_SET_URN}#1",
            "postMapped": {"id": "v1cur"},
            "current": True,
        },
        {
            "variantUrn": f"{fixtures.SCORE_SET_URN}#1",
            "postMapped": {"id": "v1old"},
            "current": False,
        },
    ]
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/mapped-variants").mock(
        return_value=httpx.Response(200, json=raw)
    )
    out = await service.get_mapped_variants(fixtures.SCORE_SET_URN)
    assert out["total"] == 2
    assert out["current_only"] is True
    assert out["ordering"] == "variant_index"
    assert all(m["current"] for m in out["mapped_variants"])
    assert [m["variant_urn"] for m in out["mapped_variants"]] == [
        f"{fixtures.SCORE_SET_URN}#1",
        f"{fixtures.SCORE_SET_URN}#2",
    ]


@respx.mock(base_url=BASE)
async def test_get_mapped_variants_current_only_false_keeps_both(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    raw = [
        {"variantUrn": f"{fixtures.SCORE_SET_URN}#1", "postMapped": {"id": "a"}, "current": True},
        {"variantUrn": f"{fixtures.SCORE_SET_URN}#1", "postMapped": {"id": "b"}, "current": False},
    ]
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/mapped-variants").mock(
        return_value=httpx.Response(200, json=raw)
    )
    out = await service.get_mapped_variants(fixtures.SCORE_SET_URN, current_only=False)
    assert out["total"] == 2


@respx.mock(base_url=BASE)
async def test_get_mapped_variants_orders_numerically_not_lexically(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # F1: the upstream list is unordered; sorting the variant_urn STRING orders
    # #1, #10, #2 (lexical), which mispairs rows when zipped against the numeric
    # scores table. Must sort numerically by the trailing #index: #1, #2, #10.
    raw = [
        {
            "variantUrn": f"{fixtures.SCORE_SET_URN}#10",
            "postMapped": {"id": "v10"},
            "current": True,
        },
        {"variantUrn": f"{fixtures.SCORE_SET_URN}#2", "postMapped": {"id": "v2"}, "current": True},
        {"variantUrn": f"{fixtures.SCORE_SET_URN}#1", "postMapped": {"id": "v1"}, "current": True},
    ]
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/mapped-variants").mock(
        return_value=httpx.Response(200, json=raw)
    )
    out = await service.get_mapped_variants(fixtures.SCORE_SET_URN, limit=10)
    indices = [m["variant_index"] for m in out["mapped_variants"]]
    assert indices == [1, 2, 10]  # NOT [1, 10, 2]
    assert out["ordering"] == "variant_index"
    # the join key is surfaced on every row so callers never zip blind
    assert out["mapped_variants"][2]["variant_urn"] == f"{fixtures.SCORE_SET_URN}#10"


@respx.mock(base_url=BASE)
async def test_get_variant_scores_rows_carry_variant_index(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # F1 join key: scores rows expose variant_index so they align with mapped
    # variants by value rather than by fragile row position.
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    out = await service.get_variant_scores(fixtures.SCORE_SET_URN, start=0, limit=3)
    assert [r["variant_index"] for r in out["rows"]] == [1, 2, 3]


@respx.mock(base_url=BASE)
async def test_get_collection(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.get(f"/collections/{fixtures.COLLECTION_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.COLLECTION_RAW)
    )
    out = await service.get_collection(fixtures.COLLECTION_URN)
    assert out["name"] == "UBE2I datasets"


@respx.mock(base_url=BASE)
async def test_diagnostics_reachable(respx_mock: respx.Router, service: MaveDBService) -> None:
    respx_mock.get("/api/version").mock(
        return_value=httpx.Response(200, json=fixtures.API_VERSION_RESPONSE)
    )
    out = await service.get_diagnostics()
    assert out["api_reachable"] is True
    assert out["api_version"] == "2026.2.4"


@respx.mock(base_url=BASE)
async def test_diagnostics_unreachable_does_not_raise(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get("/api/version").mock(return_value=httpx.Response(503, text="down"))
    out = await service.get_diagnostics()
    assert out["api_reachable"] is False
    assert "error" in out


@respx.mock(base_url=BASE)
async def test_search_experiments_bare_list_paged_client_side(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    # The real endpoint returns a bare list of ALL matches and ignores limit/offset,
    # so the service must page client-side (total = full-list length).
    items = [{"urn": f"urn:mavedb:0000000{i}-a", "title": f"E{i}"} for i in range(1, 6)]
    respx_mock.post("/experiments/search").mock(return_value=httpx.Response(200, json=items))
    out = await service.search_experiments("x", limit=2, offset=0)
    assert out["total"] == 5
    assert out["returned"] == 2
    assert out["truncated"] is True
    assert out["next_offset"] == 2
    assert [r["urn"] for r in out["results"]] == [
        "urn:mavedb:00000001-a",
        "urn:mavedb:00000002-a",
    ]
