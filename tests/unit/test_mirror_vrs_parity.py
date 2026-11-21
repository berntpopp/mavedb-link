"""Issue #140: mirror and live give the same VRS ids and rows for one query.

The dump ships the BARE post-mapped VRS digest (``mavedb.post_mapped_vrs_digest``);
live serves ``ga4gh:VA.<digest>``. The mirror normalises on read (no rebuild), and
the cross-dataset rollup is always live, because only live matches across levels.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data.hybrid import HybridClient
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.ingest.builder import build_database
from mavedb_link.services.mavedb_service import MaveDBService
from tests.dump_fixture import CALIBRATED_URN, write_mini_dump
from tests.fixtures import BASE_URL

DIGEST = "MINI_digest2xxxxxxxxxxxxxxxxxxxx"
VRS = f"ga4gh:VA.{DIGEST}"
VARIANT = f"{CALIBRATED_URN}#2"

#: GET /variants/vrs/{id}: live matches across levels (here a protein-level set).
ROLLUP: list[dict[str, Any]] = [
    {"variantUrn": VARIANT, "clingenAlleleId": "CA999002", "vrsId": VRS, "level": "genomic"},
    {
        "variantUrn": "urn:mavedb:00000068-0-1#3173",
        "clingenAlleleId": "PA107198",
        "vrsId": "ga4gh:VA.glY3wBhYCdd2tWuUDEvjBgqTQLGAbrTU",
        "level": "protein",
    },
]


@pytest.fixture
async def hybrid(tmp_path: Path) -> AsyncIterator[HybridClient]:
    db = tmp_path / "mavedb.sqlite"
    build_database(write_mini_dump(tmp_path), db, zenodo_record="18511521")
    repo = MirrorRepository.open(db)
    assert repo is not None
    client = HybridClient(MaveDBApiConfig(base_url=BASE_URL, max_retries=0), repository=repo)
    yield client
    await client.aclose()


def _mock_live(mock: respx.Router) -> None:
    mock.get(f"/variants/{VARIANT.replace('#', '%23')}").mock(
        return_value=httpx.Response(200, json={"urn": VARIANT, "postMapped": {"id": VRS}})
    )
    mock.get(f"/variants/vrs/{VRS.replace(':', '%3A')}").mock(
        return_value=httpx.Response(200, json=ROLLUP)
    )


async def test_find_variant_by_urn_same_ids_and_rows_mirror_and_live(
    hybrid: HybridClient,
) -> None:
    live = MaveDBClient(MaveDBApiConfig(base_url=BASE_URL, max_retries=0))
    try:
        with respx.mock(base_url=BASE_URL) as mock:
            _mock_live(mock)
            mirror_out = await MaveDBService(hybrid).find_variant(variant_urn=VARIANT, enrich=False)
            live_out = await MaveDBService(live).find_variant(variant_urn=VARIANT, enrich=False)
    finally:
        await live.aclose()
    assert mirror_out["vrs_id"] == live_out["vrs_id"] == VRS
    assert mirror_out["hits"] == live_out["hits"]
    assert mirror_out["total"] == live_out["total"] == 2


async def test_bare_digest_input_is_accepted(hybrid: HybridClient) -> None:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        _mock_live(mock)
        out = await MaveDBService(hybrid).find_variant(DIGEST, enrich=False)
    assert out["vrs_id"] == VRS
    assert out["total"] == 2


def test_mirror_rows_carry_the_prefixed_id(hybrid: HybridClient) -> None:
    assert hybrid.mapped_vrs_for_variant(VARIANT) == VRS
    assert {r["vrs_id"] for r in hybrid.vrs_for_hgvs("p.met1arg", gene="BRCA2")} == {VRS}
    assert hybrid.score_set_mapped_variants(CALIBRATED_URN)[1]["postMapped"]["id"] == VRS
