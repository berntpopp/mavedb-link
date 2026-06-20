"""resolve_gene_identity: cache hit, live, and degrade-to-mirror on timeout."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mavedb_link.services import resolvers


class _SlowMirrorClient:
    def __init__(self, *, slow: bool) -> None:
        self._slow = slow

    def gene_identity(self, symbol: str) -> dict[str, Any] | None:
        return {"symbol": symbol, "organism": "Homo sapiens"}

    async def get_json(self, path: str, *, params: Any = None) -> Any:
        if self._slow:
            await asyncio.sleep(10)  # exceeds the timeout -> degrade
        return {
            "symbol": "BRCA1",
            "name": "BRCA1 DNA repair associated",
            "hgncId": "HGNC:1100",
            "scoreSets": [{"urn": "urn:mavedb:1-a-1"}],
        }


@pytest.fixture(autouse=True)
def _clear() -> None:
    resolvers.clear_gene_identity_cache()


@pytest.mark.asyncio
async def test_live_then_cache() -> None:
    client = _SlowMirrorClient(slow=False)
    raw, source = await resolvers.resolve_gene_identity(client, "BRCA1")
    assert raw["hgncId"] == "HGNC:1100" and source == "live"
    raw2, source2 = await resolvers.resolve_gene_identity(client, "BRCA1")
    assert source2 == "cache" and raw2["hgncId"] == "HGNC:1100"


@pytest.mark.asyncio
async def test_timeout_degrades_to_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolvers, "GENE_IDENTITY_TIMEOUT_S", 0.05)
    client = _SlowMirrorClient(slow=True)
    raw, source = await resolvers.resolve_gene_identity(client, "BRCA1")
    assert source == "mirror" and raw == {"symbol": "BRCA1", "organism": "Homo sapiens"}
