"""Resolver tools: find_variant, get_hgvs_validation, get_classified_variants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from mavedb_link.constants import (
    DEFAULT_CLASSIFIED_LIMIT,
    DEFAULT_FIND_LIMIT,
    MAX_CLASSIFIED_LIMIT,
    MAX_FIND_LIMIT,
)
from mavedb_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from mavedb_link.mcp.envelope import McpErrorContext, run_mcp_tool
from mavedb_link.mcp.next_commands import (
    after_find_variant,
    after_get_classified_variants,
    after_get_hgvs_validation,
)
from mavedb_link.mcp.schemas import (
    CLASSIFIED_VARIANTS_SCHEMA,
    FIND_VARIANT_SCHEMA,
    HGVS_VALIDATION_SCHEMA,
)
from mavedb_link.mcp.service_adapters import get_mavedb_service
from mavedb_link.mcp.tools._common import ResponseMode, ScoreSetUrnStr

if TYPE_CHECKING:
    from fastmcp import FastMCP

_VrsId = Annotated[
    str,
    Field(
        description="A GA4GH VRS allele id (starts 'ga4gh:'). Get one from "
        "get_mapped_variants (vrs_id) or get_variant_score.",
        examples=["ga4gh:VA.ZkAN2DOM70rwo9uvpOkCtlM8qVb-gYYw"],
    ),
]
_FindLimit = Annotated[
    int, Field(ge=1, le=MAX_FIND_LIMIT, description="Max cross-dataset hits (default 25).")
]
_ClassifiedLimit = Annotated[
    int, Field(ge=1, le=MAX_CLASSIFIED_LIMIT, description="Max variants (default 100).")
]
_Offset = Annotated[int, Field(ge=0, description="Rows to skip for forward paging (default 0).")]


def register_resolver_tools(mcp: FastMCP) -> None:
    """Register the cross-dataset / validation / by-class resolver tools."""

    @mcp.tool(
        name="find_variant",
        title="Find Variant Across Score Sets",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=FIND_VARIANT_SCHEMA,
        tags={"mave", "variant", "vrs", "cross-dataset"},
        description=(
            "Find ONE GA4GH VRS allele across EVERY MaveDB score set — the same "
            "variant's functional measurements wherever it was assayed. Pass a VRS "
            "id (ga4gh:VA…); each hit carries its score_set_urn, variant_urn, "
            "ClinGen Allele ID, and (when enrich=true, default) the score + "
            "calibrated classifications. ClinGen IDs are not accepted upstream — "
            "map them first via get_mapped_variants. Paged via offset/limit. "
            "Signature: find_variant(vrs_id, only_current=, enrich=, limit=, offset=, response_mode=)."
        ),
    )
    async def find_variant(
        vrs_id: _VrsId,
        only_current: Annotated[
            bool, Field(description="Keep only current genome mappings (default true).")
        ] = True,
        enrich: Annotated[
            bool, Field(description="Attach each hit's score + classifications (default true).")
        ] = True,
        limit: _FindLimit = DEFAULT_FIND_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().find_variant(
                vrs_id,
                only_current=only_current,
                enrich=enrich,
                limit=limit,
                offset=offset,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_find_variant(payload)
            return payload

        return await run_mcp_tool(
            "find_variant",
            call,
            context=McpErrorContext(
                "find_variant", arguments={"vrs_id": vrs_id}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="get_hgvs_validation",
        title="Get HGVS Validation",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=HGVS_VALIDATION_SCHEMA,
        tags={"mave", "variant", "hgvs", "validation"},
        description=(
            "Validate an HGVS variant string against MaveDB's validator. Returns "
            "{variant, valid, message}: a valid string -> valid=true; a "
            "parseable-but-wrong one -> valid=false WITH the upstream reason "
            "(e.g. reference-base disagreement, missing transcript accession) so "
            "you can fix it before a lookup fails. Not a normalizer. "
            "Signature: get_hgvs_validation(variant, response_mode=)."
        ),
    )
    async def get_hgvs_validation(
        variant: Annotated[
            str,
            Field(
                description="An HGVS string (accession-prefixed recommended).",
                examples=["NM_000059.4:c.8167G>A", "NP_000050.3:p.Asp2723His"],
            ),
        ],
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_hgvs_validation(variant)
            payload.setdefault("_meta", {})["next_commands"] = after_get_hgvs_validation(payload)
            return payload

        return await run_mcp_tool(
            "get_hgvs_validation",
            call,
            context=McpErrorContext(
                "get_hgvs_validation", arguments={"variant": variant}, response_mode=response_mode
            ),
        )

    @mcp.tool(
        name="get_classified_variants",
        title="Get Classified Variants",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=CLASSIFIED_VARIANTS_SCHEMA,
        tags={"mave", "variant", "calibration", "acmg"},
        description=(
            "Return a score set's variants grouped into a calibrated functional "
            "class — e.g. every 'abnormal' (PS3) or 'normal' (BS3) variant — "
            "without paging the whole table. Resolves the primary calibration "
            "(or a given calibration_urn); filter with classification=abnormal|"
            "normal|not_specified (omit for all). Each variant carries its score, "
            "HGVS, class label, and ACMG criterion. Paged via offset/limit. "
            "Signature: get_classified_variants(urn, classification=, calibration_urn=, "
            "limit=, offset=, response_mode=)."
        ),
    )
    async def get_classified_variants(
        urn: ScoreSetUrnStr,
        classification: Annotated[
            str | None,
            Field(
                default=None,
                description="Filter to one class: abnormal | normal | not_specified.",
                examples=["abnormal", "normal"],
            ),
        ] = None,
        calibration_urn: Annotated[
            str | None,
            Field(
                default=None,
                description="A specific calibration URN; omit to use the primary calibration.",
            ),
        ] = None,
        limit: _ClassifiedLimit = DEFAULT_CLASSIFIED_LIMIT,
        offset: _Offset = 0,
        response_mode: ResponseMode = "compact",
    ) -> dict[str, Any]:
        async def call() -> dict[str, Any]:
            payload = await get_mavedb_service().get_classified_variants(
                urn,
                classification=classification,
                calibration_urn=calibration_urn,
                limit=limit,
                offset=offset,
                response_mode=response_mode,
            )
            payload.setdefault("_meta", {})["next_commands"] = after_get_classified_variants(
                payload
            )
            return payload

        return await run_mcp_tool(
            "get_classified_variants",
            call,
            context=McpErrorContext(
                "get_classified_variants", arguments={"urn": urn}, response_mode=response_mode
            ),
        )
