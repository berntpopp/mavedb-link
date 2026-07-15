"""Discovery tools: get_server_capabilities, get_diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastmcp.tools.tool import ToolResult
from pydantic import Field

from mavedb_link.buildinfo import build_info
from mavedb_link.mcp import metrics
from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.capabilities import collect_tool_signatures, project_capabilities
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import after_capabilities, after_diagnostics
from mavedb_link.mcp.service_adapters import get_mavedb_service

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_discovery_tools(mcp: FastMCP) -> None:
    """Register the discovery tools on a FastMCP instance."""

    @mcp.tool(
        name="get_server_capabilities",
        title="Get Server Capabilities",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"discovery"},
        description=(
            "Return the mavedb-link discovery surface: identity/build, the tool "
            "list WITH call signatures, response modes, recommended workflows, the "
            "MaveDB identifier scheme, the error taxonomy, and limits. detail='full' "
            "adds the full policy notes. Call this first in a cold session, or read "
            "mavedb://tools / mavedb://capabilities. "
            "Signature: get_server_capabilities(detail=)."
        ),
    )
    async def get_server_capabilities(
        detail: Annotated[
            Literal["summary", "full"],
            Field(description="summary (default, light) or full (adds policy notes)."),
        ] = "summary",
    ) -> dict[str, Any] | ToolResult:
        async def call() -> dict[str, Any]:
            signatures = await collect_tool_signatures(mcp)
            payload = project_capabilities(detail, signatures)
            payload.setdefault("_meta", {})["next_commands"] = after_capabilities()
            return payload

        return await run_mcp_tool(
            "get_server_capabilities",
            call,
            context=McpErrorContext("get_server_capabilities"),
        )

    @mcp.tool(
        name="get_diagnostics",
        title="Get MaveDB Diagnostics",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"discovery"},
        description=(
            "Report upstream MaveDB API reachability and version (live check of "
            "GET /api/version), the configured base URL, this build's provenance, "
            "and a runtime block (request/error counts and latency percentiles "
            "p50/p95/p99). Use this to confirm the API is up or diagnose an "
            "upstream_unavailable error. "
            "Signature: get_diagnostics()."
        ),
    )
    async def get_diagnostics() -> dict[str, Any] | ToolResult:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_diagnostics()
            payload["build"] = build_info()
            payload["runtime"] = metrics.snapshot()
            payload.setdefault("_meta", {})["next_commands"] = after_diagnostics(payload)
            return payload

        return await run_mcp_tool(
            "get_diagnostics",
            call,
            context=McpErrorContext("get_diagnostics"),
        )
