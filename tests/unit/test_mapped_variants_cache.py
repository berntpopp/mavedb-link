"""Service-level mapped-variant reads served from HybridClient lazy cache."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data.hybrid import HybridClient
from mavedb_link.data.mapped_cache import MappedVariantCache
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.ingest.builder import build_database
from mavedb_link.services.mavedb_service import MaveDBService
from tests.dump_fixture import CALIBRATED_URN, write_mini_dump
from tests.fixtures import BASE_URL


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[MaveDBService]:
    db = tmp_path / "mavedb.sqlite"
    build_database(write_mini_dump(tmp_path), db, zenodo_record="18511521")
    repo = MirrorRepository.open(db)
    assert repo is not None
    cache = MappedVariantCache(tmp_path / "mapped.sqlite", data_version="1:v4", lru_sets=16)
    client = HybridClient(
        MaveDBApiConfig(base_url=BASE_URL, cache_ttl=0, cache_size=0, max_retries=0),
        repository=repo,
        cache=cache,
    )
    svc = MaveDBService(client)
    yield svc
    await svc.aclose()


def _raw_mapped_variants() -> list[dict[str, Any]]:
    return [
        {
            "variantUrn": f"{CALIBRATED_URN}#1",
            "preMapped": {"id": "pre1"},
            "postMapped": {
                "id": "ga4gh:VA.cached1",
                "type": "Allele",
                "location": {
                    "sequenceReference": {
                        "refgetAccession": "SQ.MINI",
                        "assembly": "GRCh38",
                    },
                    "start": 32316460,
                    "end": 32316461,
                },
                "state": {"sequence": "T"},
            },
            "clingenAlleleId": "CA999001",
            "current": True,
            "vrsVersion": "2.0",
        },
        {
            "variantUrn": f"{CALIBRATED_URN}#2",
            "postMapped": {"id": "ga4gh:VA.old"},
            "current": False,
        },
    ]


async def test_get_mapped_variants_standard_and_full_reuse_lazy_cache(
    service: MaveDBService,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{CALIBRATED_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_raw_mapped_variants())
        )

        first = await service.get_mapped_variants(CALIBRATED_URN, response_mode="standard")
        second = await service.get_mapped_variants(CALIBRATED_URN, response_mode="standard")
        full = await service.get_mapped_variants(CALIBRATED_URN, response_mode="full")

    assert route.call_count == 1
    assert first == second
    assert first["total"] == 1
    assert first["mapped_variants"][0]["post_mapped"] == {
        "assembly": "GRCh38",
        "sequence_id": "SQ.MINI",
        "start": 32316460,
        "end": 32316461,
        "alt": "T",
    }
    assert full["total"] == 1
    assert full["mapped_variants"][0]["post_mapped"]["id"] == "ga4gh:VA.cached1"


async def test_get_mapped_variants_current_false_uses_cached_raw_list(
    service: MaveDBService,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{CALIBRATED_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_raw_mapped_variants())
        )

        await service.get_mapped_variants(CALIBRATED_URN, response_mode="standard")
        out = await service.get_mapped_variants(CALIBRATED_URN, current_only=False)

    assert route.call_count == 1
    assert out["total"] == 2
    assert [m["current"] for m in out["mapped_variants"]] == [True, False]


async def test_get_diagnostics_reports_mapped_cache_status(service: MaveDBService) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/version").mock(
            return_value=httpx.Response(200, json={"name": "MaveDB API", "version": "test"})
        )
        out = await service.get_diagnostics()

    assert out["api_reachable"] is True
    assert out["cache"] == {"enabled": True, "on_disk": 0, "lru_size": 0, "data_version": "1:v4"}
