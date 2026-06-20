"""Live smoke tests against the real MaveDB API (``-m integration``).

These hit https://api.mavedb.org and validate that the wrapper's assumptions
about response shapes still hold. Excluded from default CI; run with
``make test-integration``.
"""

from __future__ import annotations

import pytest

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.services.mavedb_service import MaveDBService

pytestmark = pytest.mark.integration

KNOWN_SCORE_SET = "urn:mavedb:00000001-a-1"
KNOWN_EXPERIMENT = "urn:mavedb:00000001-a"
#: A score set with curated calibrations (BRCA2 HDR, IGVF controls).
CALIBRATED_SCORE_SET = "urn:mavedb:00001224-a-1"
#: A BRCA1 functional set with >1000 variants (drives the ordering-alignment check).
ORDERING_SCORE_SET = "urn:mavedb:00000081-a-1"
#: A BRCA2 SGE set storing accession-prefixed hgvs_nt and null hgvs_pro (F5).
SGE_SCORE_SET = "urn:mavedb:00001242-a-1"


@pytest.fixture
async def live_service() -> MaveDBService:
    svc = MaveDBService(MaveDBClient(MaveDBApiConfig()))
    yield svc
    await svc.aclose()


async def test_diagnostics(live_service: MaveDBService) -> None:
    diag = await live_service.get_diagnostics()
    assert diag["api_reachable"] is True
    assert diag["api_version"]


async def test_get_score_set(live_service: MaveDBService) -> None:
    out = await live_service.get_score_set(KNOWN_SCORE_SET, response_mode="standard")
    assert out["urn"] == KNOWN_SCORE_SET
    assert out["targets"]
    assert out["num_variants"] and out["num_variants"] > 0


async def test_search_score_sets(live_service: MaveDBService) -> None:
    out = await live_service.search_score_sets("BRCA1", limit=5)
    assert out["returned"] >= 1
    assert all(r.get("urn") for r in out["results"])


async def test_get_variant_scores(live_service: MaveDBService) -> None:
    out = await live_service.get_variant_scores(KNOWN_SCORE_SET, start=0, limit=5)
    assert out["urn"] == KNOWN_SCORE_SET
    assert out["returned"] >= 1
    assert "score" in out["columns"]
    # at least one row carries a numeric score
    assert any(isinstance(r.get("score"), float) for r in out["rows"])


async def test_get_gene_score_sets(live_service: MaveDBService) -> None:
    out = await live_service.get_gene_score_sets("BRCA1", limit=5)
    assert out["gene"].get("symbol")
    assert isinstance(out["score_sets"], list)


async def test_get_experiment(live_service: MaveDBService) -> None:
    out = await live_service.get_experiment(KNOWN_EXPERIMENT)
    assert out["urn"] == KNOWN_EXPERIMENT
    assert out.get("score_set_urns")


async def test_get_mapped_variants(live_service: MaveDBService) -> None:
    out = await live_service.get_mapped_variants(KNOWN_SCORE_SET, limit=3)
    # mapped variants may be empty for some score sets, but the call must succeed
    assert "mapped_variants" in out
    assert out["returned"] <= 3


async def test_search_experiments_paging_honoured(live_service: MaveDBService) -> None:
    # The upstream endpoint returns ALL matches and ignores limit; the service must
    # still honour limit by paging client-side.
    out = await live_service.search_experiments("BRCA1", limit=2)
    assert out["returned"] <= 2
    assert out["total"] >= out["returned"]
    assert all(r.get("urn") for r in out["results"])


# --- interpretation layer (validates the calibration shapes still hold) --------


async def test_get_score_set_surfaces_calibrations(live_service: MaveDBService) -> None:
    out = await live_service.get_score_set(CALIBRATED_SCORE_SET, response_mode="standard")
    calibrations = out.get("score_calibrations")
    assert calibrations, "expected curated calibrations on a calibrated score set"
    classes = calibrations[0]["classifications"]
    assert any(c.get("acmg") in ("PS3", "BS3") for c in classes)


async def test_get_variant_scores_classifies_rows(live_service: MaveDBService) -> None:
    out = await live_service.get_variant_scores(CALIBRATED_SCORE_SET, start=0, limit=50)
    assert out.get("calibrations")
    assert any("classification" in r for r in out["rows"])


async def test_get_classified_variants_live(live_service: MaveDBService) -> None:
    out = await live_service.get_classified_variants(
        CALIBRATED_SCORE_SET, classification="abnormal", limit=5
    )
    assert out["calibration_urn"]
    assert out["returned"] >= 1
    assert all(v["classification"] == "abnormal" for v in out["variants"])


async def test_get_score_distribution_live(live_service: MaveDBService) -> None:
    out = await live_service.get_score_distribution(CALIBRATED_SCORE_SET, score=1.0)
    assert out["n"] > 0
    assert out["histogram"]
    assert 0.0 <= out["query"]["percentile"] <= 100.0


async def test_find_variant_live(live_service: MaveDBService) -> None:
    mapped = await live_service.get_mapped_variants(CALIBRATED_SCORE_SET, limit=5)
    vrs = next((m["vrs_id"] for m in mapped["mapped_variants"] if m.get("vrs_id")), None)
    if not vrs:
        pytest.skip("no VRS id available to drive the cross-dataset lookup")
    out = await live_service.find_variant(vrs, enrich=False)
    assert out["total"] >= 1
    assert all(h.get("score_set_urn") for h in out["hits"])


async def test_get_hgvs_validation_live(live_service: MaveDBService) -> None:
    valid = await live_service.get_hgvs_validation("NM_000059.4:c.8167G>A")
    assert valid["valid"] is True
    invalid = await live_service.get_hgvs_validation("NM_000059.4:c.8167A>G")
    assert invalid["valid"] is False
    assert invalid["message"]


# --- remediation contracts (this session) --------------------------------------


async def test_mapped_variants_ordering_is_numeric(live_service: MaveDBService) -> None:
    # F1: rows are ordered numerically by variant_index, NOT lexically. A lexical
    # sort would yield 1,10,100,...,2 -> NOT monotonically increasing.
    out = await live_service.get_mapped_variants(ORDERING_SCORE_SET, limit=30)
    assert out["ordering"] == "variant_index"
    indices = [m["variant_index"] for m in out["mapped_variants"] if m.get("variant_index")]
    assert indices == sorted(indices)
    assert indices, "expected mapped variants with parseable indices"


async def test_variant_score_both_paths_same_shape_live(live_service: MaveDBService) -> None:
    # F2: resolve the same variant by URN and by its hgvs -> identical key sets.
    scores = await live_service.get_variant_scores(KNOWN_SCORE_SET, start=0, limit=20)
    row = next(
        (
            r
            for r in scores["rows"]
            if r.get("accession") and (r.get("hgvs_nt") or r.get("hgvs_pro"))
        ),
        None,
    )
    assert row, "expected a row with an hgvs string to drive the lookup"
    hgvs = row.get("hgvs_nt") or row.get("hgvs_pro")
    by_urn = await live_service.get_variant_score(row["accession"])
    by_hgvs = await live_service.get_variant_score(KNOWN_SCORE_SET, hgvs=hgvs)
    assert set(by_urn) == set(by_hgvs)
    assert by_urn["resolved_by"] == "variant_urn"
    assert by_hgvs["resolved_by"] == "hgvs"
    assert any(v.get("variant_urn") == row["accession"] for v in by_hgvs["variants"])


async def test_bare_hgvs_resolves_accession_prefixed_set(live_service: MaveDBService) -> None:
    # F5: a bare c. form resolves a set that stores accession-prefixed hgvs_nt.
    out = await live_service.get_variant_score(SGE_SCORE_SET, hgvs="c.8168A>G")
    assert out["match_count"] >= 1
    assert any(":c.8168A>G" in (v.get("hgvs_nt") or "") for v in out["variants"])


async def test_score_set_standard_vs_full_tiering(live_service: MaveDBService) -> None:
    # F8: standard elides the heavy free text + caps author lists; full carries them.
    standard = await live_service.get_score_set(KNOWN_SCORE_SET, response_mode="standard")
    full = await live_service.get_score_set(KNOWN_SCORE_SET, response_mode="full")
    assert "method_text" not in standard
    assert full.get("method_text")
    std_pub = standard["publications"]["primary"][0]
    assert "authors" not in std_pub
    assert full["publications"]["primary"][0].get("authors") is not None


async def test_diagnostics_advertises_interpretation(live_service: MaveDBService) -> None:
    # A4: diagnostics names the interpretation surface.
    diag = await live_service.get_diagnostics()
    assert diag["interpretation"]["calibration_supported"] is True
    assert "get_variant_scores" in diag["interpretation"]["surfaced_by"]
