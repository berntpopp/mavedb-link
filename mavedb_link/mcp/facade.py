"""MCP facade for mavedb-link: assemble the FastMCP instance with all tools."""

from __future__ import annotations

from fastmcp import FastMCP

from mavedb_link.mcp.capabilities import register_capability_resources
from mavedb_link.mcp.middleware import ArgValidationMiddleware
from mavedb_link.mcp.resources import MAVEDB_SERVER_INSTRUCTIONS
from mavedb_link.mcp.tools import (
    register_collection_tools,
    register_discovery_tools,
    register_experiment_tools,
    register_gene_tools,
    register_score_set_tools,
    register_variant_tools,
)


def create_mavedb_mcp() -> FastMCP:
    """Build a FastMCP instance with all mavedb-link tools, resources, middleware."""
    mcp = FastMCP(
        name="mavedb-link",
        instructions=MAVEDB_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )

    register_discovery_tools(mcp)
    register_score_set_tools(mcp)
    register_variant_tools(mcp)
    register_gene_tools(mcp)
    register_experiment_tools(mcp)
    register_collection_tools(mcp)
    register_capability_resources(mcp)
    mcp.add_middleware(ArgValidationMiddleware())

    return mcp
