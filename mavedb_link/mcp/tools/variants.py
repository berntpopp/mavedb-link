"""Variant-data tools: get_variant_scores, get_mapped_variants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from mavedb_link.constants import (
    DEFAULT_MAPPED_LIMIT,
    DEFAULT_SCORES_LIMIT,
    MAX_MAPPED_LIMIT,
    MAX_SCORES_LIMIT,
)
from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import (
    after_get_mapped_variants,
    after_get_score_distribution,
    after_get_variant_score,
    after_get_variant_scores,
)
from mavedb_link.mcp.schemas import (
    MAPPED_VARIANTS_SCHEMA,
    SCORE_DISTRIBUTION_SCHEMA,
    VARIANT_SCORE_SCHEMA,
    VARIANT_SCORES_SCHEMA,
)
from mavedb_link.mcp.service_adapters import get_mavedb_service
from mavedb_link.mcp.tools._common import ResponseMode, ScoreSetUrnStr, VariantLookupUrn

if TYPE_CHECKING:
    from fastmcp import FastMCP

_Start = Annotated[int, Field(ge=0, description="Row offset into the score table (default 0).")]
_ScoresLimit = Annotated[
    int, Field(ge=1, le=MAX_SCORES_LIMIT, description="Max score rows (default 100).")
]
_MappedLimit = Annotated[
    int, Field(ge=1, le=MAX_MAPPED_LIMIT, description="Max mapped variants (default 50).")
]
_Offset = Annotated[int, Field(ge=0, description="Rows to skip for forward paging (default 0).")]


def register_variant_tools(mcp: FastMCP) -> None:
    """Register the variant-data tools on a FastMCP instance."""

    @mcp.tool(
        name="get_variant_scores",
        title="Get Variant Scores",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=VARIANT_SCORES_SCHEMA,
        tags={"mave", "variant", "score", "functional-assay"},
        description=(
            "Return the quantitative variant-by-variant score table for a score set "
            "(urn:mavedb:...-a-1) as parsed rows: each row carries accession, HGVS "
            "(hgvs_nt/hgvs_splice/hgvs_pro), the numeric score, and score-set "
            "specific columns. Paged via start/limit; NA values become null. Page "
            "forward with start=next_start. "
            "Signature: get_variant_scores(urn, start=, limit=, drop_na_columns=, response_mode=)."
        ),
    )
    async def get_variant_scores(
        urn: ScoreSetUrnStr,
        start: _Start = 0,
        limit: _ScoresLimit = DEFAULT_SCORES_LIMIT,
        drop_na_columns: Annotated[
            bool, Field(description="Drop columns that are entirely NA (default false).")
        ] = False,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_variant_scores(
                urn,
                start=start,
                limit=limit,
                drop_na_columns=drop_na_columns,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_get_variant_scores(payload)
            return payload

        return await run_mcp_tool(
            "get_variant_scores",
            call,
            context=McpErrorContext(
                "get_variant_scores", arguments={"urn": urn}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="get_variant_score",
        title="Get Variant Score",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=VARIANT_SCORE_SCHEMA,
        tags={"mave", "variant", "score", "single-variant"},
        description=(
            "Look up the functional score for ONE variant without paging the whole "
            "table. Pass a full variant URN (urn:mavedb:...-a-1#2) to resolve it "
            "directly, OR a score-set URN plus hgvs= (e.g. 'c.8168A>G' or "
            "'p.Arg1699Trp') to scan that score set's table for the matching row(s). "
            "Returns the variant's score (+ hgvs, score_set_urn). The fast path for "
            "'what is the score for this variant?'. "
            "Signature: get_variant_score(urn, hgvs=, response_mode=)."
        ),
    )
    async def get_variant_score(
        urn: VariantLookupUrn,
        hgvs: Annotated[
            str | None,
            Field(
                default=None,
                description="HGVS string (hgvs_nt or hgvs_pro) to match when urn is a "
                "score-set URN; omit when urn is a full variant URN.",
                examples=["c.8168A>G", "p.Arg1699Trp"],
            ),
        ] = None,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_variant_score(
                urn, hgvs=hgvs, response_mode=response_mode
            )
            payload.setdefault("_meta", {})["next_commands"] = after_get_variant_score(payload)
            return payload

        return await run_mcp_tool(
            "get_variant_score",
            call,
            context=McpErrorContext(
                "get_variant_score", arguments={"urn": urn}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="get_score_distribution",
        title="Get Score Distribution",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=SCORE_DISTRIBUTION_SCHEMA,
        tags={"mave", "variant", "score", "distribution", "statistics"},
        description=(
            "Summarise a score set's score distribution server-side (MaveDB has no "
            "stats endpoint): n, min/max, mean, median, quartiles, stdev, and a "
            "10-bin histogram — a compact summary INSTEAD of paging the whole "
            "table. Pass score= to locate that value (its percentile + calibrated "
            "classification). Carries the calibration thresholds when present. "
            "Signature: get_score_distribution(urn, score=, response_mode=)."
        ),
    )
    async def get_score_distribution(
        urn: ScoreSetUrnStr,
        score: Annotated[
            float | None,
            Field(
                default=None,
                description="A score to locate within the distribution (percentile + class).",
            ),
        ] = None,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_score_distribution(
                urn, score=score, response_mode=response_mode
            )
            payload.setdefault("_meta", {})["next_commands"] = after_get_score_distribution(payload)
            return payload

        return await run_mcp_tool(
            "get_score_distribution",
            call,
            context=McpErrorContext(
                "get_score_distribution", arguments={"urn": urn}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="get_mapped_variants",
        title="Get Mapped Variants",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=MAPPED_VARIANTS_SCHEMA,
        tags={"mave", "variant", "vrs", "mapping"},
        description=(
            "Return the genome-mapped GA4GH VRS alleles for a score set's variants "
            "(urn:mavedb:...-a-1), each with its source variant URN, VRS allele id, "
            "ClinGen Allele ID, and current flag — the bridge from assay coordinates "
            "to reference-genome/clinical coordinates. Rows are ordered by variant_urn "
            "(aligns with get_variant_scores); current_only (default true) collapses "
            "the current/superseded pair to one row per variant. Paged via offset/limit. "
            "Signature: get_mapped_variants(urn, current_only=, limit=, offset=, response_mode=)."
        ),
    )
    async def get_mapped_variants(
        urn: ScoreSetUrnStr,
        current_only: Annotated[
            bool, Field(description="Keep only the current mapping per variant (default true).")
        ] = True,
        limit: _MappedLimit = DEFAULT_MAPPED_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_mapped_variants(
                urn,
                current_only=current_only,
                limit=limit,
                offset=offset,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_get_mapped_variants(payload)
            return payload

        return await run_mcp_tool(
            "get_mapped_variants",
            call,
            context=McpErrorContext(
                "get_mapped_variants", arguments={"urn": urn}, response_mode=response_mode
            ),
        )
