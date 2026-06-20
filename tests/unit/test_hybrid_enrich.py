"""HybridClient lazy mapped-variant enrichment and cache-backed lookup helpers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data.hybrid import HybridClient, mapped_cache_status
from mavedb_link.data.mapped_cache import MappedVariantCache
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.exceptions import ServiceUnavailableError
from tests.fixtures import BASE_URL

COMPLETE_URN = "urn:mavedb:00000001-a-1"
FAILED_URN = "urn:mavedb:00000002-a-1"
NO_STATE_URN = "urn:mavedb:00000003-a-1"


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
        INSERT INTO meta VALUES (1, '2026-01-01T00:00:00+00:00', '18511521', 'v4', 3, 0, '2026-01-02T00:00:00+00:00');
        INSERT INTO hgvs_index VALUES ('urn:mavedb:00000001-a-1', 'urn:mavedb:00000001-a-1#1', NULL, 'p.met1leu', NULL);
        INSERT INTO gene_index VALUES ('BRCA1', 'BRCA1', 'urn:mavedb:00000001-a-1', 'Homo sapiens', 'protein_coding');
        """
    )
    records = [
        {"urn": COMPLETE_URN, "mappingState": "complete"},
        {"urn": FAILED_URN, "mappingState": "failed"},
        {"urn": NO_STATE_URN},
    ]
    con.executemany(
        "INSERT INTO score_set (urn, record_json) VALUES (?, ?)",
        [(r["urn"], json.dumps(r)) for r in records],
    )
    return MirrorRepository(con)


@pytest.fixture
async def hybrid(tmp_path: Path, repo: MirrorRepository) -> AsyncIterator[HybridClient]:
    cache = MappedVariantCache(tmp_path / "mapped.sqlite", data_version="1:v4", lru_sets=16)
    client = HybridClient(
        MaveDBApiConfig(base_url=BASE_URL, cache_ttl=0, cache_size=0, max_retries=0),
        repository=repo,
        cache=cache,
    )
    yield client
    await client.aclose()


def _mapped_items(urn: str = COMPLETE_URN) -> list[dict[str, Any]]:
    return [
        {
            "variantUrn": f"{urn}#1",
            "postMapped": {"id": "ga4gh:VA.cached"},
            "clingenAlleleId": "CA123",
            "current": True,
        }
    ]


async def test_ensure_mapped_variants_fetches_once_then_uses_cache(
    hybrid: HybridClient,
) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{COMPLETE_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        first = await hybrid.ensure_mapped_variants(COMPLETE_URN)
        second = await hybrid.ensure_mapped_variants(COMPLETE_URN)

    assert first == _mapped_items()
    assert second == _mapped_items()
    assert route.call_count == 1


async def test_failed_mapping_state_caches_empty_without_live_call(
    hybrid: HybridClient,
) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        route = mock.get(f"/score-sets/{FAILED_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_mapped_items(FAILED_URN))
        )
        items = await hybrid.ensure_mapped_variants(FAILED_URN)

    assert items == []
    assert route.call_count == 0


async def test_absent_mapping_state_still_fetches_live(hybrid: HybridClient) -> None:
    expected = _mapped_items(NO_STATE_URN)
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{NO_STATE_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=expected)
        )
        items = await hybrid.ensure_mapped_variants(NO_STATE_URN)

    assert items == expected
    assert route.call_count == 1


async def test_concurrent_cold_calls_single_flight(hybrid: HybridClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{COMPLETE_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        results = await asyncio.gather(
            *(hybrid.ensure_mapped_variants(COMPLETE_URN) for _ in range(5))
        )

    assert results == [_mapped_items()] * 5
    assert route.call_count == 1


async def test_live_errors_propagate_and_do_not_cache(hybrid: HybridClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get(f"/score-sets/{COMPLETE_URN}/mapped-variants").mock(
            return_value=httpx.Response(503, json={"detail": "upstream down"})
        )
        with pytest.raises(ServiceUnavailableError):
            await hybrid.ensure_mapped_variants(COMPLETE_URN)

    stats = hybrid.mapped_cache_stats()
    assert stats is not None
    assert stats["on_disk"] == 0


async def test_helpers_read_vrs_from_cached_mapped_variants(hybrid: HybridClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get(f"/score-sets/{COMPLETE_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        await hybrid.ensure_mapped_variants(COMPLETE_URN)

    assert hybrid.mapped_vrs_for_variant(f"{COMPLETE_URN}#1") == "ga4gh:VA.cached"
    assert hybrid.score_set_mapped_variants(COMPLETE_URN) == _mapped_items()
    assert hybrid.vrs_for_hgvs("p.met1leu", gene="BRCA1") == [
        {
            "variant_urn": f"{COMPLETE_URN}#1",
            "score_set_urn": COMPLETE_URN,
            "vrs_id": "ga4gh:VA.cached",
        }
    ]


async def test_mapped_cache_status_reports_cache_stats(hybrid: HybridClient) -> None:
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get(f"/score-sets/{COMPLETE_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=_mapped_items())
        )
        await hybrid.ensure_mapped_variants(COMPLETE_URN)

    status = mapped_cache_status(hybrid)
    assert status["enabled"] is True
    assert status["on_disk"] == 1
    assert status["lru_size"] == 1
    assert status["data_version"] == "1:v4"
