"""Issue #136: the current upstream variant record + VRS lookup shapes.

MaveDB replaced ``GET /variants/{urn}``'s ``data.score_data``/``mappedVariants``
body with ``scores``/``counts``/``postMapped`` and moved the cross-dataset lookup
from ``/mapped-variants/vrs/{id}`` (now a 301) to ``/variants/vrs/{id}``. Reading
the old keys returned ``score: null`` and no VRS, so a found variant had no score
and a live HGVS probe answered a false ``not_found``. Fixtures are trimmed from
live responses (2026-11-21).
"""

from __future__ import annotations

from typing import Any

import httpx
import respx

from mavedb_link.services.mavedb_service import MaveDBService
from mavedb_link.services.shaping import shape_single_variant
from tests import fixtures

BASE = fixtures.BASE_URL
SET_URN = "urn:mavedb:00001277-a-1"
VARIANT_URN = f"{SET_URN}#1166"
VARIANT_URN_ENCODED = VARIANT_URN.replace("#", "%23")
GENOMIC_VRS = "ga4gh:VA.Zu35OMDtyrvaSQpsrlEmb9P0GoYtEhzI"

VARIANT_RECORD: dict[str, Any] = {
    "urn": VARIANT_URN,
    "scores": {"score": 0.967049981773921, "replicate_1_score": 1.04338616518609},
    "counts": {},
    "assayLevel": "genomic",
    "targetHgvs": "NM_000546.6:c.455C>T",
    "referenceHgvs": "NC_000017.11:g.7675157G>A",
    "clingenAlleleId": "CA000204",
    "preMapped": {"id": "ga4gh:VA.pre", "type": "Allele"},
    "postMapped": {"id": GENOMIC_VRS, "type": "Allele"},
    "mapped": {
        "genomic": {"hgvs": "NC_000017.11:g.7675157G>A"},
        "cdna": {"hgvs": "NM_000546.6:c.455C>T"},
        "protein": {"hgvs": "NP_000537.3:p.Pro152Leu"},
    },
    "isCurrent": True,
}

#: GET /variants/vrs/{id}: lean rows; protein-level sets come back for a genomic VRS.
VRS_ROWS: list[dict[str, Any]] = [
    {
        "variantUrn": "urn:mavedb:00000068-0-1#3173",
        "clingenAlleleId": "PA107198",
        "vrsId": "ga4gh:VA.glY3wBhYCdd2tWuUDEvjBgqTQLGAbrTU",
        "level": "protein",
    },
    {
        "variantUrn": VARIANT_URN,
        "clingenAlleleId": "CA000204",
        "vrsId": GENOMIC_VRS,
        "level": "genomic",
    },
]

SCORES_CSV = (
    "accession,hgvs_nt,hgvs_splice,hgvs_pro,score\n"
    f"{VARIANT_URN},NM_000546.6:c.455C>T,NA,NA,0.967049981773921\n"
)


def test_shape_single_variant_reads_current_record() -> None:
    compact = shape_single_variant(VARIANT_RECORD, "compact")
    assert compact["score"] == 0.967049981773921
    assert compact["score_set_urn"] == SET_URN
    assert compact["hgvs_nt"] == "NM_000546.6:c.455C>T"
    assert compact["hgvs_pro"] == "NP_000537.3:p.Pro152Leu"
    standard = shape_single_variant(VARIANT_RECORD, "standard")
    assert standard["score_data"]["score"] == 0.967049981773921
    assert [m["vrs_id"] for m in standard["mapped_variants"]] == [GENOMIC_VRS]


@respx.mock(base_url=BASE)
async def test_get_variant_score_by_urn_returns_score(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get(f"/variants/{VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=VARIANT_RECORD)
    )
    respx_mock.get(f"/score-sets/{SET_URN}").mock(return_value=httpx.Response(200, json={}))
    out = await service.get_variant_score(VARIANT_URN)
    assert out["variants"][0]["score"] == 0.967049981773921


@respx.mock(base_url=BASE)
async def test_find_variant_transcript_coding_hgvs_resolves_live(
    respx_mock: respx.Router, service: MaveDBService
) -> None:
    respx_mock.get("/genes/TP53").mock(
        return_value=httpx.Response(200, json={"scoreSets": [{"urn": SET_URN}]})
    )
    respx_mock.get(f"/score-sets/{SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=SCORES_CSV)
    )
    respx_mock.get(f"/score-sets/{SET_URN}").mock(return_value=httpx.Response(200, json={}))
    respx_mock.get(f"/variants/{VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=VARIANT_RECORD)
    )
    respx_mock.get("/score-sets/urn:mavedb:00000068-0-1").mock(
        return_value=httpx.Response(200, json={})
    )
    respx_mock.get("/variants/urn:mavedb:00000068-0-1%233173").mock(
        return_value=httpx.Response(200, json={**VARIANT_RECORD, "urn": VRS_ROWS[0]["variantUrn"]})
    )
    respx_mock.get(f"/variants/vrs/{GENOMIC_VRS.replace(':', '%3A')}").mock(
        return_value=httpx.Response(200, json=VRS_ROWS)
    )
    out = await service.find_variant(hgvs="ENST00000269305.9:c.455C>T", gene="TP53")
    assert out["vrs_id"] == GENOMIC_VRS
    assert out["total"] == 2
    assert {h["score_set_urn"] for h in out["hits"]} == {"urn:mavedb:00000068-0-1", SET_URN}
    assert all(h["score"] is not None for h in out["hits"])
    assert {h["vrs_id"] for h in out["hits"]} == {r["vrsId"] for r in VRS_ROWS}


@respx.mock(base_url=BASE)
async def test_find_variant_not_found_offers_gene_score_sets(
    respx_mock: respx.Router, facade: Any, structured: Any
) -> None:
    respx_mock.get("/genes/TP53").mock(return_value=httpx.Response(200, json={"scoreSets": []}))
    res = await facade.call_tool("find_variant", {"variant": "c.455C>T", "gene_symbol": "TP53"})
    payload = structured(res)
    assert payload["error_code"] == "not_found"
    assert "(" not in payload["message"]  # no call syntax for the gateway to rewrite
    assert payload["_meta"]["next_commands"][0] == {
        "tool": "get_gene_score_sets",
        "arguments": {"symbol": "TP53"},
    }


def test_superseded_record_keeps_its_own_mapping() -> None:
    # isCurrent is score-set currency, not mapping currency: a superseded set's
    # variant must still resolve its VRS (find_variant(variant_urn=)).
    shaped = shape_single_variant({**VARIANT_RECORD, "isCurrent": False}, "standard")
    assert [m["vrs_id"] for m in shaped["mapped_variants"]] == [GENOMIC_VRS]
