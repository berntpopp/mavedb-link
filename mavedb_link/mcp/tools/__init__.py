"""MCP tool registration fan-out for mavedb-link."""

from __future__ import annotations

from mavedb_link.mcp.tools.collections import register_collection_tools
from mavedb_link.mcp.tools.discovery import register_discovery_tools
from mavedb_link.mcp.tools.experiments import register_experiment_tools
from mavedb_link.mcp.tools.genes import register_gene_tools
from mavedb_link.mcp.tools.resolvers import register_resolver_tools
from mavedb_link.mcp.tools.score_sets import register_score_set_tools
from mavedb_link.mcp.tools.variants import register_variant_tools

__all__ = [
    "register_collection_tools",
    "register_discovery_tools",
    "register_experiment_tools",
    "register_gene_tools",
    "register_resolver_tools",
    "register_score_set_tools",
    "register_variant_tools",
]
