"""Collection tool: get_collection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import after_get_collection
from mavedb_link.mcp.schemas import COLLECTION_SCHEMA
from mavedb_link.mcp.service_adapters import get_mavedb_service
from mavedb_link.mcp.tools._common import ResponseMode, UrnStr

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_collection_tools(mcp: FastMCP) -> None:
    """Register the collection tool on a FastMCP instance."""

    @mcp.tool(
        name="get_collection",
        title="Get Collection",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=COLLECTION_SCHEMA,
        tags={"mave", "collection"},
        description=(
            "Return a curated MaveDB collection by URN: its name, description, "
            "badge, and the member experiment and score-set URNs. Collections group "
            "related datasets (e.g. by gene, consortium, or theme). Open a member "
            "with get_score_set. "
            "Signature: get_collection(urn, response_mode=)."
        ),
    )
    async def get_collection(
        urn: UrnStr, response_mode: ResponseMode = "compact"
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_collection(urn, response_mode=response_mode)
            payload.setdefault("_meta", {})["next_commands"] = after_get_collection(payload)
            return payload

        return await run_mcp_tool(
            "get_collection",
            call,
            context=McpErrorContext(
                "get_collection", arguments={"urn": urn}, response_mode=response_mode
            ),
        )
