"""Gene tool: get_gene_score_sets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastmcp.tools.tool import ToolResult
from pydantic import Field

from mavedb_link.constants import DEFAULT_GENE_LIMIT, MAX_GENE_LIMIT
from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import after_get_gene_score_sets
from mavedb_link.mcp.service_adapters import get_mavedb_service
from mavedb_link.mcp.tools._common import ResponseMode, SymbolStr

if TYPE_CHECKING:
    from fastmcp import FastMCP

_Limit = Annotated[int, Field(ge=1, le=MAX_GENE_LIMIT, description="Max score sets (default 20).")]
_Offset = Annotated[int, Field(ge=0, description="Score sets to skip for paging (default 0).")]


def register_gene_tools(mcp: FastMCP) -> None:
    """Register the gene tool on a FastMCP instance."""

    @mcp.tool(
        name="get_gene_score_sets",
        title="Get Gene Score Sets",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"mave", "gene", "variant", "score-set"},
        description=(
            "Resolve an HGNC gene symbol (e.g. BRCA1) to its gene identity "
            "(name, HGNC id, location) AND every published MaveDB score set that "
            "targets it — the COMPLETE set, unioned from HGNC resolution and the "
            "target-name facet and deduped by URN (see the coverage block). The "
            "fastest complete way to find all MAVE data for a gene. Paged via "
            "offset/limit. "
            "Signature: get_gene_score_sets(gene_symbol, limit=, offset=, response_mode=)."
        ),
    )
    async def get_gene_score_sets(
        gene_symbol: SymbolStr,
        limit: _Limit = DEFAULT_GENE_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any] | ToolResult:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_gene_score_sets(
                gene_symbol, limit=limit, offset=offset, response_mode=response_mode
            )
            payload.setdefault("_meta", {})["next_commands"] = after_get_gene_score_sets(payload)
            return payload

        return await run_mcp_tool(
            "get_gene_score_sets",
            call,
            context=McpErrorContext(
                "get_gene_score_sets",
                arguments={"gene_symbol": gene_symbol},
                response_mode=response_mode,
            ),
        )
