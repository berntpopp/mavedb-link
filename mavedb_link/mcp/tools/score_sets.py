"""Score-set tools: search_score_sets, get_score_set."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from mavedb_link.constants import DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT
from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import after_get_score_set, after_search_score_sets
from mavedb_link.mcp.schemas import SCORE_SET_SCHEMA, SEARCH_SCORE_SETS_SCHEMA
from mavedb_link.mcp.service_adapters import get_mavedb_service
from mavedb_link.mcp.tools._common import ResponseMode, ScoreSetUrnStr, SearchText, StringList

if TYPE_CHECKING:
    from fastmcp import FastMCP

_Limit = Annotated[int, Field(ge=1, le=MAX_SEARCH_LIMIT, description="Max hits (default 25).")]
_Offset = Annotated[int, Field(ge=0, description="Rows to skip for forward paging (default 0).")]


def register_score_set_tools(mcp: FastMCP) -> None:
    """Register the score-set search/record tools on a FastMCP instance."""

    @mcp.tool(
        name="search_score_sets",
        title="Search Score Sets",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=SEARCH_SCORE_SETS_SCHEMA,
        tags={"mave", "variant", "score-set", "search"},
        description=(
            "Search MaveDB score sets (the datasets carrying scored variants) by "
            "free text and facets — target gene(s), organism, target type, and "
            "author. Returns score-set hits {urn, title, num_variants, targets, "
            "license, ...} plus a pagination block. Organism/target-type facets are "
            "null-inclusive by default (records with unknown metadata are KEPT and "
            "_meta.facet_excluded reports the drops); pass facet_mode='strict' to "
            "also drop unknown-metadata records. This is the MaveDB front door. "
            "Signature: search_score_sets(text=, targets=, target_organism_names=, "
            "target_types=, authors=, facet_mode=, published=, limit=, offset=, "
            "response_mode=)."
        ),
    )
    async def search_score_sets(
        text: SearchText = None,
        targets: StringList = None,
        target_organism_names: StringList = None,
        target_types: StringList = None,
        authors: StringList = None,
        facet_mode: Annotated[
            Literal["inclusive", "strict"],
            Field(
                description="'inclusive' (default; keep unknown-metadata records) or "
                "'strict' (drop them).",
                examples=["inclusive", "strict"],
            ),
        ] = "inclusive",
        published: Annotated[
            bool, Field(description="Restrict to published records (default true).")
        ] = True,
        limit: _Limit = DEFAULT_SEARCH_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().search_score_sets(
                text,
                published=published,
                targets=targets,
                target_organism_names=target_organism_names,
                target_types=target_types,
                authors=authors,
                facet_mode=facet_mode,
                limit=limit,
                offset=offset,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_search_score_sets(
                text, payload
            )
            return payload

        return await run_mcp_tool(
            "search_score_sets",
            call,
            context=McpErrorContext(
                "search_score_sets", arguments={"text": text}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="get_score_set",
        title="Get Score Set",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=SCORE_SET_SCHEMA,
        tags={"mave", "variant", "score-set"},
        description=(
            "Return a MaveDB score-set record by URN: title, description, target "
            "gene(s) with external IDs, parent experiment, publications, the "
            "per-record license, variant count, and (standard/full) dataset columns "
            "and method/abstract text. Get the actual scores with get_variant_scores. "
            "Signature: get_score_set(urn, response_mode=)."
        ),
    )
    async def get_score_set(
        urn: ScoreSetUrnStr, response_mode: ResponseMode = "compact"
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_score_set(urn, response_mode=response_mode)
            payload.setdefault("_meta", {})["next_commands"] = after_get_score_set(payload)
            return payload

        return await run_mcp_tool(
            "get_score_set",
            call,
            context=McpErrorContext(
                "get_score_set", arguments={"urn": urn}, response_mode=response_mode
            ),
        )
