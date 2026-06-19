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
