"""find_variant resolves a bare HGVS string via the mirror, then the live probe."""

from __future__ import annotations

from typing import Any

import pytest

from mavedb_link.exceptions import AmbiguousQueryError, InvalidInputError
from mavedb_link.services import resolvers


class _MirrorClient:
    """Stub HybridClient: mirror resolves hgvs, get_json answers the VRS rollup."""

    def __init__(self, hgvs_rows: list[dict[str, Any]]) -> None:
        self._hgvs_rows = hgvs_rows

    def vrs_for_hgvs(
        self, core: str, full: str | None = None, *, gene: str | None = None
    ) -> list[dict[str, Any]]:
        return self._hgvs_rows

    async def get_json(self, path: str, *, params: Any = None) -> Any:
        return [
            {
                "variantUrn": "urn:mavedb:1-a-1#1",
                "postMapped": {"id": "ga4gh:VA.x"},
                "current": True,
            }
        ]


@pytest.mark.asyncio
async def test_find_variant_by_hgvs_mirror() -> None:
    client = _MirrorClient(
        [
            {
                "variant_urn": "urn:mavedb:1-a-1#1",
                "score_set_urn": "urn:mavedb:1-a-1",
                "vrs_id": "ga4gh:VA.x",
            }
        ]
    )
    out = await resolvers.find_variant(client, hgvs="p.Asp2723His", gene="BRCA1", enrich=False)
    assert out["resolved_by"] == "hgvs"
    assert out["vrs_id"] == "ga4gh:VA.x"
    assert "resolved_vrs" not in out  # single allele: carried by vrs_id, kept lean
    assert out["hgvs_input"] == "p.Asp2723His"
    assert out["probe_truncated"] is False
    assert out["hits"] and out["hits"][0]["vrs_id"] == "ga4gh:VA.x"


@pytest.mark.asyncio
async def test_find_variant_hgvs_multi_vrs_with_gene_lists_all() -> None:
    client = _MirrorClient(
        [
            {
                "variant_urn": "urn:mavedb:1-a-1#1",
                "score_set_urn": "urn:mavedb:1-a-1",
                "vrs_id": "ga4gh:VA.a",
            },
            {
                "variant_urn": "urn:mavedb:1-a-2#1",
                "score_set_urn": "urn:mavedb:1-a-2",
                "vrs_id": "ga4gh:VA.b",
            },
        ]
    )
    out = await resolvers.find_variant(client, hgvs="p.Asp2723His", gene="BRCA1", enrich=False)
    assert out["resolved_by"] == "hgvs"
    assert out["resolved_vrs"] == ["ga4gh:VA.a", "ga4gh:VA.b"]
    assert out["vrs_id"] == "ga4gh:VA.a"


@pytest.mark.asyncio
async def test_find_variant_hgvs_ambiguous_without_gene() -> None:
    client = _MirrorClient(
        [
            {
                "variant_urn": "urn:mavedb:1-a-1#1",
                "score_set_urn": "urn:mavedb:1-a-1",
                "vrs_id": "ga4gh:VA.a",
            },
            {
                "variant_urn": "urn:mavedb:2-a-1#1",
                "score_set_urn": "urn:mavedb:2-a-1",
                "vrs_id": "ga4gh:VA.b",
            },
        ]
    )
    with pytest.raises(AmbiguousQueryError):
        await resolvers.find_variant(client, hgvs="p.Asp2723His", enrich=False)


@pytest.mark.asyncio
async def test_find_variant_hgvs_miss_requires_gene_for_live_probe() -> None:
    client = _MirrorClient([])  # mirror miss
    with pytest.raises(InvalidInputError):
        await resolvers.find_variant(client, hgvs="p.Asp2723His", enrich=False)


class _CountingMirror:
    """Mirror stub that counts every cache lookup and upstream fetch it services."""

    def __init__(self) -> None:
        self.vrs_calls = 0
        self.get_json_calls = 0

    def vrs_for_hgvs(
        self, core: str, full: str | None = None, *, gene: str | None = None
    ) -> list[dict[str, Any]]:
        self.vrs_calls += 1
        return [
            {
                "variant_urn": "urn:mavedb:1-a-1#1",
                "score_set_urn": "urn:mavedb:1-a-1",
                "vrs_id": "ga4gh:VA.x",
            }
        ]

    async def get_json(self, path: str, *, params: Any = None) -> Any:
        self.get_json_calls += 1
        return []


@pytest.mark.asyncio
async def test_find_variant_rejects_whitespace_padded_oversize_hgvs_before_mirror() -> None:
    # F-09 gate2 (Codex re-gate): find_variant(hgvs=) must bound the RAW hgvs length
    # BEFORE any strip/normalize, mirror cache lookup, or live probe. A valid core padded
    # with thousands of leading whitespace exceeds the bound yet strips under the cap --
    # it must be rejected up front so the mirror cache is never consulted and nothing is
    # forwarded upstream (the earlier F-09 fix bounded get_hgvs_validation only).
    from mavedb_link.constants import MAX_HGVS_VARIANT_CHARS

    client = _CountingMirror()
    padded = " " * (MAX_HGVS_VARIANT_CHARS + 1000) + "p.Asp2723His"
    assert len(padded) > MAX_HGVS_VARIANT_CHARS
    with pytest.raises(InvalidInputError) as exc:
        await resolvers.find_variant(client, hgvs=padded, gene="BRCA1", enrich=False)
    assert exc.value.field == "variant"
    assert client.vrs_calls == 0  # mirror hgvs cache never consulted
    assert client.get_json_calls == 0  # nothing forwarded upstream
    # The fixed error must not echo the caller's (stripped) payload.
    assert "p.Asp2723His" not in str(exc.value)
