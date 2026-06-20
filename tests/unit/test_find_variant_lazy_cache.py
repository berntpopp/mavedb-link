"""find_variant warm paths for the lazy mapped-variant cache."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import respx

from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data.hybrid import HybridClient
from mavedb_link.data.mapped_cache import MappedVariantCache
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.services.mavedb_service import MaveDBService
from tests.fixtures import BASE_URL

SCORE_SET_URN = "urn:mavedb:00000001-a-1"
VARIANT_URN = f"{SCORE_SET_URN}#1"
VRS_ID = "ga4gh:VA.lazy"


@pytest.fixture
def repo() -> MirrorRepository:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE meta (
            id INTEGER PRIMARY KEY,
            dump_as_of TEXT,
            zenodo_record TEXT,
            zenodo_version TEXT,
            score_set_count INTEGER,
            mapped_variant_count INTEGER,
            build_utc TEXT
        );
        CREATE TABLE score_set (urn TEXT PRIMARY KEY, record_json TEXT NOT NULL);
        CREATE TABLE mapped_variant (
            variant_urn TEXT,
            score_set_urn TEXT,
            vrs_id TEXT,
            clingen_allele_id TEXT,
            post_mapped_hgvs_g TEXT,
            post_mapped_hgvs_p TEXT,
            post_mapped_hgvs_c TEXT
        );
        CREATE TABLE hgvs_index (
            score_set_urn TEXT,
            variant_urn TEXT,
            hgvs_nt TEXT,
            hgvs_pro TEXT,
            hgvs_splice TEXT
        );
        CREATE TABLE gene_index (
            gene_symbol_upper TEXT,
            gene_symbol TEXT,
            score_set_urn TEXT,
            organism TEXT,
            category TEXT
        );
        INSERT INTO meta VALUES (1, '2026-01-01T00:00:00+00:00', '18511521', 'v4', 1, 0, '2026-01-02T00:00:00+00:00');
        INSERT INTO hgvs_index VALUES ('urn:mavedb:00000001-a-1', 'urn:mavedb:00000001-a-1#1', NULL, 'p.met1leu', NULL);
        INSERT INTO gene_index VALUES ('BRCA1', 'BRCA1', 'urn:mavedb:00000001-a-1', 'Homo sapiens', 'protein_coding');
        """
    )
    record = {"urn": SCORE_SET_URN, "mappingState": "complete"}
    con.execute(
        "INSERT INTO score_set (urn, record_json) VALUES (?, ?)",
        (SCORE_SET_URN, json.dumps(record)),
    )
    return MirrorRepository(con)


@pytest.fixture
async def service(tmp_path: Path, repo: MirrorRepository) -> AsyncIterator[MaveDBService]:
    cache = MappedVariantCache(tmp_path / "mapped.sqlite", data_version="1:v4", lru_sets=16)
    client = HybridClient(
        MaveDBApiConfig(base_url=BASE_URL, cache_ttl=0, cache_size=0, max_retries=0),
        repository=repo,
        cache=cache,
    )
    svc = MaveDBService(client)
    yield svc
    await svc.aclose()


def _mapped_items() -> list[dict[str, Any]]:
    return [
        {
            "variantUrn": VARIANT_URN,
            "postMapped": {"id": VRS_ID},
            "clingenAlleleId": "CA123",
            "current": True,
        }
    ]


async def test_find_variant_variant_urn_warms_and_reuses_cache(
    service: MaveDBService,
) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        mapped_route = mock.get(f"/score-sets/{SCORE_SET_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        rollup_route = mock.get(f"/mapped-variants/vrs/{quote(VRS_ID, safe='')}").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        variant_route = mock.get(f"/variants/{VARIANT_URN.replace('#', '%23')}").mock(
            return_value=httpx.Response(500, json={"detail": "variant path not expected"})
        )

        first = await service.find_variant(variant_urn=VARIANT_URN, enrich=False)
        second = await service.find_variant(variant_urn=VARIANT_URN, enrich=False)

    assert first["resolved_by"] == "variant_urn"
    assert second["vrs_id"] == VRS_ID
    assert mapped_route.call_count == 1
    assert rollup_route.call_count == 2
    assert variant_route.call_count == 0


async def test_find_variant_hgvs_warms_gene_sets_and_reuses_cache(
    service: MaveDBService,
) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        mapped_route = mock.get(f"/score-sets/{SCORE_SET_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        rollup_route = mock.get(f"/mapped-variants/vrs/{quote(VRS_ID, safe='')}").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        gene_route = mock.get("/genes/BRCA1").mock(
            return_value=httpx.Response(500, json={"detail": "live probe not expected"})
        )

        first = await service.find_variant(hgvs="p.Met1Leu", gene="BRCA1", enrich=False)
        second = await service.find_variant(hgvs="p.Met1Leu", gene="BRCA1", enrich=False)

    assert first["resolved_by"] == "hgvs"
    assert second["vrs_id"] == VRS_ID
    assert mapped_route.call_count == 1
    assert rollup_route.call_count == 2
    assert gene_route.call_count == 0
