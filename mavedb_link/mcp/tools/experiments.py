"""Experiment tools: get_experiment, search_experiments."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from mavedb_link.constants import DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT
from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import after_get_experiment, after_search_experiments
from mavedb_link.mcp.schemas import EXPERIMENT_SCHEMA, SEARCH_EXPERIMENTS_SCHEMA
from mavedb_link.mcp.service_adapters import get_mavedb_service
from mavedb_link.mcp.tools._common import (
    AuthorsFilter,
    OrganismsFilter,
    ResponseMode,
    SearchText,
    TargetsFilter,
    TargetTypesFilter,
    UrnStr,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

_Limit = Annotated[int, Field(ge=1, le=MAX_SEARCH_LIMIT, description="Max hits (default 25).")]
_Offset = Annotated[int, Field(ge=0, description="Rows to skip for forward paging (default 0).")]


def register_experiment_tools(mcp: FastMCP) -> None:
    """Register the experiment tools on a FastMCP instance."""

    @mcp.tool(
        name="get_experiment",
        title="Get Experiment",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=EXPERIMENT_SCHEMA,
        tags={"mave", "experiment"},
        description=(
            "Return a MaveDB experiment record by URN (urn:mavedb:00000001-a): "
            "title, description, parent experiment set, the child score-set URNs, "
            "keywords, and publications. An experiment groups one or more score sets "
            "from one assay context. Open a child dataset with get_score_set. "
            "Signature: get_experiment(urn, response_mode=)."
        ),
    )
    async def get_experiment(
        urn: UrnStr, response_mode: ResponseMode = "compact"
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_experiment(urn, response_mode=response_mode)
            payload.setdefault("_meta", {})["next_commands"] = after_get_experiment(payload)
            return payload

        return await run_mcp_tool(
            "get_experiment",
            call,
            context=McpErrorContext(
                "get_experiment", arguments={"urn": urn}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="search_experiments",
        title="Search Experiments",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=SEARCH_EXPERIMENTS_SCHEMA,
        tags={"mave", "experiment", "search"},
        description=(
            "Search MaveDB experiments by free text (author facet, plus target "
            "facets — targets/target_organism_names/target_types — derived from the "
            "score-set search and grouped by parent experiment). A gene-token query "
            "re-ranks experiments whose score sets target the gene above abstract "
            "namesakes (reranked_by:'target_gene'). Returns experiment hits {urn, "
            "score_set_urns, ...} plus a pagination block (paged client-side). Use "
            "search_score_sets when you want datasets/scores; use this for the "
            "assay-context grouping. "
            "Signature: search_experiments(text=, targets=, target_organism_names=, "
            "target_types=, authors=, published=, limit=, offset=, response_mode=)."
        ),
    )
    async def search_experiments(
        text: SearchText = None,
        targets: TargetsFilter = None,
        target_organism_names: OrganismsFilter = None,
        target_types: TargetTypesFilter = None,
        authors: AuthorsFilter = None,
        published: Annotated[
            bool, Field(description="Restrict to published records (default true).")
        ] = True,
        limit: _Limit = DEFAULT_SEARCH_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().search_experiments(
                text,
                published=published,
                targets=targets,
                target_organism_names=target_organism_names,
                target_types=target_types,
                authors=authors,
                limit=limit,
                offset=offset,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_search_experiments(
                text, payload
            )
            return payload

        return await run_mcp_tool(
            "search_experiments",
            call,
            context=McpErrorContext(
                "search_experiments", arguments={"text": text}, response_mode=response_mode
            ),
        )
