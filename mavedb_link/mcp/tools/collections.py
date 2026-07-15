"""Collection tool: get_collection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastmcp.tools.tool import ToolResult
from pydantic import Field

from mavedb_link.constants import DEFAULT_COLLECTION_LIMIT, MAX_COLLECTION_LIMIT
from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import after_get_collection
from mavedb_link.mcp.service_adapters import get_mavedb_service
from mavedb_link.mcp.tools._common import CollectionUrnStr, ResponseMode

if TYPE_CHECKING:
    from fastmcp import FastMCP

_Limit = Annotated[
    int, Field(ge=1, le=MAX_COLLECTION_LIMIT, description="Max member score sets (default 100).")
]
_Offset = Annotated[int, Field(ge=0, description="Members to skip for forward paging (default 0).")]


def register_collection_tools(mcp: FastMCP) -> None:
    """Register the collection tool on a FastMCP instance."""

    @mcp.tool(
        name="get_collection",
        title="Get Collection",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"mave", "collection"},
        description=(
            "Return a curated MaveDB collection by URN: its name, description, "
            "badge, num_score_sets/num_experiments, and the member experiment and "
            "score-set URNs. Collections group related datasets (e.g. by gene, "
            "consortium, or theme). The member lists are PAGED (limit/offset, "
            "truncated/next_offset) so large collections stay light. Open a member "
            "with get_score_set. "
            "Signature: get_collection(urn, limit=, offset=, response_mode=)."
        ),
    )
    async def get_collection(
        urn: CollectionUrnStr,
        limit: _Limit = DEFAULT_COLLECTION_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any] | ToolResult:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_collection(
                urn, limit=limit, offset=offset, response_mode=response_mode
            )
            payload.setdefault("_meta", {})["next_commands"] = after_get_collection(payload)
            return payload

        return await run_mcp_tool(
            "get_collection",
            call,
            context=McpErrorContext(
                "get_collection", arguments={"urn": urn}, response_mode=response_mode
            ),
        )
