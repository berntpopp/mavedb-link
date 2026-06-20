"""Hybrid client: mirror-first serving, live fallback, and honest provenance.

Verifies the mirror answers the intercepted reads (no HTTP), a mirror-miss falls
through to the live API, and the envelope surfaces _meta.data_source
(mirror | live | mixed) + mirror_as_of.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from mavedb_link.config import MaveDBApiConfig
from mavedb_link.data import provenance
from mavedb_link.data.hybrid import HybridClient
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.ingest.builder import build_database
from mavedb_link.mcp.envelope import run_mcp_tool
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures
from tests.dump_fixture import CALIBRATED_URN, DUMP_AS_OF, write_mini_dump
from tests.fixtures import BASE_URL, SCORE_SET_URN


@pytest.fixture
async def hybrid(tmp_path: Path) -> AsyncIterator[HybridClient]:
    db = tmp_path / "mavedb.sqlite"
    build_database(write_mini_dump(tmp_path), db, zenodo_record="18511521")
    repo = MirrorRepository.open(db)
    assert repo is not None
    client = HybridClient(MaveDBApiConfig(base_url=BASE_URL, max_retries=0), repository=repo)
    yield client
    await client.aclose()


async def test_score_set_record_served_from_mirror(hybrid: HybridClient) -> None:
    provenance.begin()
    rec = await hybrid.get_json(f"/score-sets/{SCORE_SET_URN}")
    assert rec["numVariants"] == 12720
    assert provenance.snapshot()["data_source"] == "mirror"


async def test_scores_csv_served_from_mirror(hybrid: HybridClient) -> None:
    provenance.begin()
    text = await hybrid.get_text(
        f"/score-sets/{SCORE_SET_URN}/scores", params={"start": 0, "limit": 10}
    )
    assert text.splitlines()[0] == "accession,hgvs_nt,hgvs_splice,hgvs_pro,score,sd,exp.score"
    assert provenance.snapshot()["data_source"] == "mirror"


async def test_search_served_from_mirror(hybrid: HybridClient) -> None:
    provenance.begin()
    resp = await hybrid.post_json("/score-sets/search", json={"text": "BRCA2"})
    assert resp["numScoreSets"] == 1
    assert resp["scoreSets"][0]["urn"] == CALIBRATED_URN


async def test_vrs_rollup_served_from_mirror(hybrid: HybridClient) -> None:
    provenance.begin()
    items = await hybrid.get_json("/mapped-variants/vrs/ga4gh%3AVA.MINI_digest1")
    assert items[0]["variantUrn"] == f"{CALIBRATED_URN}#1"
    assert items[0]["postMapped"]["id"] == "ga4gh:VA.MINI_digest1"


async def test_mirror_miss_falls_through_to_live(hybrid: HybridClient) -> None:
    unknown = "urn:mavedb:09999999-a-1"
    provenance.begin()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{unknown}").mock(
            return_value=httpx.Response(
                200, json={"urn": unknown, "numVariants": 1, "license": {"shortName": "CC0"}}
            )
        )
        rec = await hybrid.get_json(f"/score-sets/{unknown}")
    assert route.called
    assert rec["urn"] == unknown
    assert provenance.snapshot()["data_source"] == "live"


async def test_envelope_reports_mirror_provenance(hybrid: HybridClient) -> None:
    svc = MaveDBService(hybrid)
    env = await run_mcp_tool("get_score_set", lambda: svc.get_score_set(SCORE_SET_URN))
    assert env["success"] is True
    assert env["_meta"]["data_source"] == "mirror"
    assert env["_meta"]["mirror_as_of"] == DUMP_AS_OF


async def test_envelope_reports_mixed_when_one_read_is_live(hybrid: HybridClient) -> None:
    # get_gene_score_sets unions the live /genes identity with the mirror target search.
    svc = MaveDBService(hybrid)
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get(url__regex=r".*/genes/UBE2I.*").mock(
            return_value=httpx.Response(200, json=fixtures.GENE_RESPONSE)
        )
        env = await run_mcp_tool("get_gene_score_sets", lambda: svc.get_gene_score_sets("UBE2I"))
    assert env["success"] is True
    assert env["_meta"]["data_source"] == "mixed"


async def test_mirror_and_live_score_set_are_shape_identical(hybrid: HybridClient) -> None:
    # The invariant: a mirror-served payload is interchangeable with the live one.
    from mavedb_link.api.client import MaveDBClient

    mirror_out = await MaveDBService(hybrid).get_score_set(SCORE_SET_URN, response_mode="standard")
    live = MaveDBClient(MaveDBApiConfig(base_url=BASE_URL, max_retries=0))
    try:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get(f"/score-sets/{SCORE_SET_URN}").mock(
                return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW)
            )
            live_out = await MaveDBService(live).get_score_set(
                SCORE_SET_URN, response_mode="standard"
            )
    finally:
        await live.aclose()
    assert mirror_out == live_out


async def test_mapped_variants_served_from_mirror(hybrid: HybridClient) -> None:
    # GAP-B: the per-set mapped-variant enumeration serves from the SQLite mirror
    # for the default (current_only, compact) read -- no live 9.7s round-trip, and
    # no respx route registered, so any live attempt would error instead.
    provenance.begin()
    out = await MaveDBService(hybrid).get_mapped_variants(CALIBRATED_URN)
    assert out["total"] == 2
    assert out["mapped_variants"][0]["variant_urn"] == f"{CALIBRATED_URN}#1"
    assert out["mapped_variants"][0]["vrs_id"] == "ga4gh:VA.MINI_digest1"
    assert out["mapped_variants"][0]["clingen_allele_id"] == "CA999001"
    assert provenance.snapshot()["data_source"] == "mirror"


async def test_mapped_variants_standard_falls_through_to_live(hybrid: HybridClient) -> None:
    # standard/full need the full VRS objects the annotation index lacks, so they
    # fall through to live. standard now returns a FLAT post_mapped genomic summary
    # (assembly/sequence_id/start/end/ref/alt) and DROPS pre_mapped; full keeps both.
    provenance.begin()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{CALIBRATED_URN}/mapped-variants").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "variantUrn": f"{CALIBRATED_URN}#1",
                        "preMapped": {"id": "pre1"},
                        "postMapped": {
                            "id": "ga4gh:VA.MINI_digest1",
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
                    }
                ],
            )
        )
        out = await MaveDBService(hybrid).get_mapped_variants(
            CALIBRATED_URN, response_mode="standard"
        )
    assert route.called
    assert provenance.snapshot()["data_source"] == "live"
    row = out["mapped_variants"][0]
    assert "pre_mapped" not in row  # trimmed at standard
    assert row["post_mapped"] == {
        "assembly": "GRCh38",
        "sequence_id": "SQ.MINI",
        "start": 32316460,
        "end": 32316461,
        "alt": "T",
    }
    assert row["vrs_version"] == "2.0"


async def test_mapped_variants_current_false_falls_through_to_live(hybrid: HybridClient) -> None:
    # The annotation index carries only CURRENT mappings; current_only=False (which
    # asks for superseded rows too) must therefore go live for completeness.
    provenance.begin()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.get(f"/score-sets/{CALIBRATED_URN}/mapped-variants").mock(
            return_value=httpx.Response(200, json=[])
        )
        await MaveDBService(hybrid).get_mapped_variants(CALIBRATED_URN, current_only=False)
    assert route.called
    assert provenance.snapshot()["data_source"] == "live"


async def test_mapped_variants_mirror_and_live_compact_interchangeable(
    hybrid: HybridClient,
) -> None:
    # The invariant: a mirror-served compact page is interchangeable with the live one.
    from mavedb_link.api.client import MaveDBClient

    mirror_out = await MaveDBService(hybrid).get_mapped_variants(CALIBRATED_URN)
    live = MaveDBClient(MaveDBApiConfig(base_url=BASE_URL, max_retries=0))
    try:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get(f"/score-sets/{CALIBRATED_URN}/mapped-variants").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "variantUrn": f"{CALIBRATED_URN}#1",
                            "postMapped": {"id": "ga4gh:VA.MINI_digest1"},
                            "clingenAlleleId": "CA999001",
                            "current": True,
                        },
                        {
                            "variantUrn": f"{CALIBRATED_URN}#2",
                            "postMapped": {"id": "ga4gh:VA.MINI_digest2"},
                            "clingenAlleleId": "CA999002",
                            "current": True,
                        },
                    ],
                )
            )
            live_out = await MaveDBService(live).get_mapped_variants(CALIBRATED_URN)
    finally:
        await live.aclose()
    assert mirror_out == live_out


async def test_find_variant_resolves_vrs_and_rollup_from_mirror(hybrid: HybridClient) -> None:
    # D.3: find_variant(variant_urn=) resolves the variant's VRS from the annotation
    # index (no live /variants fetch) and rolls it up from the mirror -- so the
    # cross-dataset consolidation path is fully mirror-served when enrich is off.
    provenance.begin()
    out = await MaveDBService(hybrid).find_variant(variant_urn=f"{CALIBRATED_URN}#1", enrich=False)
    assert out["resolved_by"] == "variant_urn"
    assert out["vrs_id"] == "ga4gh:VA.MINI_digest1"
    assert out["hits"][0]["variant_urn"] == f"{CALIBRATED_URN}#1"
    assert provenance.snapshot()["data_source"] == "mirror"


async def test_vrs_for_hgvs_served_from_mirror(hybrid: HybridClient) -> None:
    # The calibrated (BRCA2) set carries hgvs_pro p.Met1Leu -> VRS digest1.
    provenance.begin()
    rows = hybrid.vrs_for_hgvs("p.met1leu", gene="BRCA2")
    assert any(r["vrs_id"] == "ga4gh:VA.MINI_digest1" for r in rows)
    assert provenance.snapshot()["data_source"] == "mirror"


async def test_vrs_for_hgvs_genomic_served_from_mirror(hybrid: HybridClient) -> None:
    rows = hybrid.vrs_for_hgvs("nc_000013.11:g.32316461a>t")
    assert [r["vrs_id"] for r in rows] == ["ga4gh:VA.MINI_digest1"]


async def test_vrs_for_hgvs_miss_returns_empty(hybrid: HybridClient) -> None:
    assert hybrid.vrs_for_hgvs("p.nonexistent999x", gene="BRCA2") == []


async def test_gene_identity_thin_from_mirror(hybrid: HybridClient) -> None:
    assert hybrid.gene_identity("BRCA2") == {"symbol": "BRCA2", "organism": "Homo sapiens"}
    assert hybrid.gene_identity("NOPE") is None


async def test_diagnostics_reports_mirror_status(hybrid: HybridClient) -> None:
    svc = MaveDBService(hybrid)
    with respx.mock(base_url=BASE_URL) as mock:
        mock.get("/api/version").mock(
            return_value=httpx.Response(200, json={"name": "mavedb-api", "version": "x"})
        )
        diag = await svc.get_diagnostics()
    assert diag["mirror"]["present"] is True
    assert diag["mirror"]["as_of"] == DUMP_AS_OF
    assert diag["mirror"]["score_set_count"] == 2
    assert diag["mirror"]["zenodo_record"] == "18511521"


async def test_diagnostics_live_only_reports_no_mirror() -> None:
    from mavedb_link.api.client import MaveDBClient

    svc = MaveDBService(MaveDBClient(MaveDBApiConfig(base_url=BASE_URL, max_retries=0)))
    try:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/api/version").mock(
                return_value=httpx.Response(200, json={"name": "x", "version": "y"})
            )
            diag = await svc.get_diagnostics()
        assert diag["mirror"] == {"present": False}
    finally:
        await svc.aclose()
