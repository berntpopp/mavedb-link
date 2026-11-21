"""Issue #137: mapped variants come from the NDJSON ``/variant-details`` export.

MaveDB removed ``GET /score-sets/{urn}/mapped-variants`` (HTTP 410) in favour of
``GET /score-sets/{urn}/variant-details``: one ``VariantDetail`` JSON object per
line, the same envelope as ``GET /variants/{urn}``, with the variant count in
``X-Total-Count``. It serves only each variant's current mapping. Records are
trimmed from the live export of urn:mavedb:00000001-a-1 (2026-11-21).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.exceptions import ServiceUnavailableError
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures

BASE = fixtures.BASE_URL
URN = "urn:mavedb:00000001-a-1"
EXPORT = f"/score-sets/{URN}/variant-details"


def _detail(index: int, vrs: str, ca: str) -> dict[str, Any]:
    allele = {"id": vrs, "type": "Allele", "digest": vrs.split(".")[-1]}
    return {
        "urn": f"{URN}#{index}",
        "scores": {"score": -0.0261761998757174},
        "clingenAlleleId": ca,
        "preMapped": allele,
        "postMapped": {**allele, "expressions": [{"value": "NP_919235.1:p.Pro73Gln"}]},
        "annotations": {vrs: {"vep": None, "gnomad": None, "clinvar": []}},
        "molecularRepresentation": {"type": "CategoricalVariant", "members": []},
        "isCurrent": True,
    }


DETAILS = [
    _detail(1112, "ga4gh:VA.1jFJZYNlMh588oYQf1w5I699ZwZX3BuR", "PA2579754171"),
    _detail(1, "ga4gh:VA.2NPpLraYtXeL3kfsHPLAkhN6H01sXyQm", "PA2579756363"),
]


def _ndjson(records: list[dict[str, Any]], total: int | None = None) -> httpx.Response:
    headers = {"content-type": "application/x-ndjson"}
    headers["x-total-count"] = str(len(records) if total is None else total)
    return httpx.Response(200, text="".join(json.dumps(r) + "\n" for r in records), headers=headers)


def _client() -> MaveDBClient:
    return MaveDBClient(MaveDBApiConfig(base_url=BASE, max_retries=0))


@respx.mock(base_url=BASE)
async def test_ensure_mapped_variants_reads_variant_details_export(
    respx_mock: respx.Router,
) -> None:
    respx_mock.get(EXPORT).mock(return_value=_ndjson(DETAILS))
    rows = await _client().ensure_mapped_variants(URN)
    assert [r["variantUrn"] for r in rows] == [f"{URN}#1112", f"{URN}#1"]
    assert rows[0]["postMapped"]["id"] == "ga4gh:VA.1jFJZYNlMh588oYQf1w5I699ZwZX3BuR"
    assert rows[0]["clingenAlleleId"] == "PA2579754171"
    assert all(r["current"] is True for r in rows)
    assert "annotations" not in rows[0]  # only the mapping is kept, not the 13 KB record


@respx.mock(base_url=BASE)
async def test_truncated_export_is_an_error_not_a_short_list(respx_mock: respx.Router) -> None:
    # Live, a mid-stream upstream drop ended the body early with a 200: 1981 of 8237.
    respx_mock.get(EXPORT).mock(return_value=_ndjson(DETAILS[:1], total=2))
    with pytest.raises(ServiceUnavailableError):
        await _client().ensure_mapped_variants(URN)


@respx.mock(base_url=BASE)
async def test_cut_off_last_line_is_an_error(respx_mock: respx.Router) -> None:
    body = json.dumps(DETAILS[0]) + "\n" + json.dumps(DETAILS[1])[:40]
    respx_mock.get(EXPORT).mock(return_value=httpx.Response(200, text=body))
    with pytest.raises(ServiceUnavailableError):
        await _client().ensure_mapped_variants(URN)


@respx.mock(base_url=BASE)
async def test_get_mapped_variants_live_and_current_only_note(
    respx_mock: respx.Router,
) -> None:
    respx_mock.get(EXPORT).mock(return_value=_ndjson(DETAILS))
    service = MaveDBService(_client())
    out = await service.get_mapped_variants(URN)
    assert [m["variant_index"] for m in out["mapped_variants"]] == [1, 1112]
    assert out["mapped_variants"][0]["vrs_id"] == "ga4gh:VA.2NPpLraYtXeL3kfsHPLAkhN6H01sXyQm"
    assert "note" not in out
    every = await service.get_mapped_variants(URN, current_only=False)
    assert every["mapped_variants"] == out["mapped_variants"]
    assert "current_only" in every["note"]


@respx.mock(base_url=BASE)
async def test_find_variant_only_current_false_says_it_has_no_effect(
    respx_mock: respx.Router,
) -> None:
    vrs = "ga4gh:VA.2NPpLraYtXeL3kfsHPLAkhN6H01sXyQm"
    respx_mock.get(f"/variants/vrs/{vrs.replace(':', '%3A')}").mock(
        return_value=httpx.Response(200, json=[{"variantUrn": f"{URN}#1", "vrsId": vrs}])
    )
    service = MaveDBService(_client())
    assert "note" not in await service.find_variant(vrs, enrich=False)
    out = await service.find_variant(vrs, enrich=False, only_current=False)
    assert out["total"] == 1
    assert "only_current" in out["note"]
