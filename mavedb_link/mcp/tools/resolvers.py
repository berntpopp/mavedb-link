"""Resolver tools: find_variant, get_hgvs_validation, get_classified_variants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

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
    str | None,
    Field(
        default=None,
        description="A GA4GH VRS allele id (starts 'ga4gh:'). Get one from "
        "get_mapped_variants (vrs_id) or get_variant_score. A variant URN passed "
        "here is auto-detected and resolved to its VRS. Omit when using variant_urn=.",
        examples=["ga4gh:VA.ZkAN2DOM70rwo9uvpOkCtlM8qVb-gYYw"],
    ),
]
_VariantUrn = Annotated[
    str | None,
    Field(
        default=None,
        description="A full variant URN ('urn:mavedb:00000001-a-1#2'). Resolved to "
        "its genome-mapped VRS internally (no map-first round-trip), then matched "
        "across every score set. Pass this OR vrs_id.",
        examples=["urn:mavedb:00000001-a-1#2"],
    ),
]
_Hgvs = Annotated[
    str | None,
    Field(
        default=None,
        description="A bare HGVS string (e.g. 'p.Asp2723His', 'c.8167G>A', or an "
        "accessioned 'NM_000059.4:c.8167G>A'). Resolved to its VRS internally via the "
        "local mirror, falling back to a capped live probe of that gene's score sets "
        "— so you do NOT pre-map it. Pass gene_symbol= alongside to disambiguate / "
        "enable the live fallback. Use this OR vrs_id OR variant_urn.",
        examples=["p.Asp2723His", "NM_000059.4:c.8167G>A"],
    ),
]
_GeneSymbol = Annotated[
    str | None,
    Field(
        default=None,
        description="HGNC gene symbol that scopes an hgvs= lookup (required when the HGVS "
        "is not in the mirror and must be resolved live). Ignored unless hgvs= is set. "
        "`gene` is accepted as a compatibility alias.",
        examples=["BRCA1", "TP53"],
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
            "Find ONE variant across EVERY MaveDB score set — the same variant's "
            "functional measurements wherever it was assayed (the cross-dataset "
            "rollup for 'every assay that measured this variant'). Pass a VRS id "
            "(ga4gh:VA…) OR a variant_urn ('urn:mavedb:…-a-1#2'): a variant URN is "
            "resolved to its VRS internally, so you do NOT need to map it first — "
            "chain straight from get_variant_score. ALSO accepts a bare hgvs= string "
            "(+ optional gene_symbol=) resolved to its VRS internally — chain straight from an "
            "HGVS the user typed, no map-first round-trip. Each hit carries its "
            "score_set_urn, variant_urn, ClinGen Allele ID, and (when enrich=true, "
            "default) the score + calibrated classifications. ClinGen Allele IDs are "
            "not accepted upstream; pass the variant_urn instead. Paged via "
            "offset/limit. Signature: find_variant(vrs_id=, variant_urn=, hgvs=, "
            "gene_symbol=, only_current=, enrich=, limit=, offset=, response_mode=)."
        ),
    )
    async def find_variant(
        vrs_id: _VrsId = None,
        variant_urn: _VariantUrn = None,
        hgvs: _Hgvs = None,
        gene_symbol: _GeneSymbol = None,
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
                variant_urn=variant_urn,
                hgvs=hgvs,
                gene=gene_symbol,
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
                "find_variant",
                arguments={
                    "vrs_id": vrs_id,
                    "variant_urn": variant_urn,
                    "hgvs": hgvs,
                    "gene_symbol": gene_symbol,
                },
                response_mode=response_mode,
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
            Literal["abnormal", "normal", "not_specified"] | None,
            Field(
                default=None,
                description="Filter to one functional class (omit for all).",
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
