"""Live end-to-end smoke check (``make smoke`` / ``python -m tests.smoke``).

Exercises the real MaveDB API through the service layer and prints a short
summary. Not a pytest test — a quick human-facing reachability check.
"""

from __future__ import annotations

import asyncio

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.services.mavedb_service import MaveDBService

SCORE_SET = "urn:mavedb:00000001-a-1"


async def _run() -> None:
    service = MaveDBService(MaveDBClient(MaveDBApiConfig()))
    try:
        diag = await service.get_diagnostics()
        print(
            f"diagnostics: reachable={diag.get('api_reachable')} version={diag.get('api_version')}"
        )

        search = await service.search_score_sets("BRCA1", limit=3)
        print(f"search 'BRCA1': {search['returned']} hit(s), total={search['total']}")
        for hit in search["results"]:
            print(f"  - {hit.get('urn')}: {hit.get('title')}")

        record = await service.get_score_set(SCORE_SET)
        print(
            f"score set {SCORE_SET}: {record.get('num_variants')} variants, license={record.get('license')}"
        )

        scores = await service.get_variant_scores(SCORE_SET, limit=3)
        print(f"scores: columns={scores['columns'][:6]} returned={scores['returned']}")
        for row in scores["rows"][:2]:
            print(f"  - {row.get('hgvs_pro')}: score={row.get('score')}")

        gene = await service.get_gene_score_sets("BRCA1", limit=3)
        print(
            f"gene BRCA1: {gene.get('total')} score set(s), {gene.get('total_scored_variants')} scored variants"
        )
    finally:
        await service.aclose()


def main() -> None:
    """Run the live smoke check."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
