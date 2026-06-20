"""Performance contract: the full-table scan is downloaded once, then cached.

get_variant_score (by hgvs) and get_score_distribution both scan the whole scores
table; the latency is dominated by that one upstream CSV download (a score-set-local
index cannot avoid it). The contract that keeps a workflow fast is therefore cache
REUSE: the table is fetched once per set and shared across repeat lookups and across
the distribution call -- so warm single-variant lookups are O(1), not O(table) (GAP-4).
"""

from __future__ import annotations

import httpx
import respx

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.constants import DISTRIBUTION_FETCH_LIMIT, VARIANT_SCAN_LIMIT
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures

BASE = fixtures.BASE_URL


def test_scan_limits_are_unified_so_the_csv_cache_is_shared() -> None:
    # Equal start+limit -> identical cache key -> the by-hgvs scan and the
    # distribution summary reuse ONE cached CSV per score set.
    assert VARIANT_SCAN_LIMIT == DISTRIBUTION_FETCH_LIMIT


@respx.mock(base_url=BASE)
async def test_repeat_lookup_downloads_scores_once(respx_mock: respx.Router) -> None:
    scores = respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
    )
    # A cache-enabled client (the production default is TTL 600s).
    client = MaveDBClient(
        MaveDBApiConfig(base_url=BASE, cache_ttl=600, cache_size=64, max_retries=0)
    )
    service = MaveDBService(client)
    try:
        await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs="c.2T>G")
        await service.get_variant_score(fixtures.SCORE_SET_URN, hgvs="c.1A>T")
        await service.get_score_distribution(fixtures.SCORE_SET_URN)
        # Three table-scanning calls on the same set -> exactly one upstream download.
        assert scores.call_count == 1
    finally:
        await client.aclose()
