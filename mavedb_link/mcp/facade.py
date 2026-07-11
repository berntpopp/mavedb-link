"""MCP facade for mavedb-link: assemble the FastMCP instance with all tools."""

from __future__ import annotations

from fastmcp import FastMCP

from mavedb_link import __version__
from mavedb_link.mcp.capabilities import register_capability_resources
from mavedb_link.mcp.middleware import ArgValidationMiddleware
from mavedb_link.mcp.notfound_guard import (
    NotFoundGuard,
    install_protocol_error_handler,
    install_validation_log_filter,
)
from mavedb_link.mcp.resources import MAVEDB_SERVER_INSTRUCTIONS
from mavedb_link.mcp.tools import (
    register_collection_tools,
    register_discovery_tools,
    register_experiment_tools,
    register_gene_tools,
    register_resolver_tools,
    register_score_set_tools,
    register_variant_tools,
)


def create_mavedb_mcp() -> FastMCP:
    """Build a FastMCP instance with all mavedb-link tools, resources, middleware."""
    mcp = FastMCP(
        name="mavedb-link",
        version=__version__,
        instructions=MAVEDB_SERVER_INSTRUCTIONS,
        mask_error_details=True,
    )

    # Guard the FastMCP-core not-found reflection surface: core echoes the
    # caller's OWN requested tool name / resource URI / prompt name (with any
    # control/zero-width/bidi/NUL code points) to the caller and to logs BEFORE
    # backend middleware runs. NotFoundGuard preflights the tool NAME (unknown ->
    # fixed name-free envelope) and fixes the on_read_resource boundary; it is
    # added FIRST so it is the OUTERMOST middleware (before ArgValidation).
    mcp.add_middleware(NotFoundGuard())
    mcp.add_middleware(ArgValidationMiddleware())

    # Layer 5: scrub FastMCP-core / MCP-SDK validation logs that would echo the
    # caller-supplied name/URI (idempotent; process-global).
    install_validation_log_filter()

    register_discovery_tools(mcp)
    register_score_set_tools(mcp)
    register_variant_tools(mcp)
    register_gene_tools(mcp)
    register_experiment_tools(mcp)
    register_collection_tools(mcp)
    register_resolver_tools(mcp)
    register_capability_resources(mcp)

    # Layer 3: install the protocol-handler backstop AFTER every tool/resource/
    # prompt is registered (so the request handlers exist). Outermost wrapper on
    # the raw CallTool/ReadResource/GetPrompt handlers — catches the unknown-tool
    # *return* path and any resource/prompt dispatch error that would echo the
    # requested name/URI (the only layer covering the unknown-prompt surface).
    install_protocol_error_handler(mcp)

    return mcp
