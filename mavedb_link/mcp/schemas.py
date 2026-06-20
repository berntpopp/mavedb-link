"""JSON output schemas for the typed MaveDB MCP tools (MCP structured output).

The schemas are deliberately **permissive** (``additionalProperties: true``,
nothing ``required``) because ``response_mode`` projects fields out and the error
envelope is returned by the same tool body and must also validate.
"""

from __future__ import annotations

from typing import Any

_META = {"type": "object", "additionalProperties": True}


def _envelope(**properties: Any) -> dict[str, Any]:
    """A permissive object schema carrying the common envelope keys + extras."""
    props: dict[str, Any] = {
        "success": {"type": "boolean"},
        "_meta": _META,
        "error_code": {"type": "string"},
        "message": {"type": "string"},
        "retryable": {"type": "boolean"},
        "recovery_action": {"type": "string"},
        "field": {"type": "string"},
        "allowed_values": {"type": "array"},
        "hint": {"type": "string"},
        "candidates": {"type": "array"},
        **properties,
    }
    return {"type": "object", "additionalProperties": True, "properties": props}


_STR = {"type": "string"}
_STR_NULL = {"type": ["string", "null"]}
_INT = {"type": "integer"}
_INT_NULL = {"type": ["integer", "null"]}
_NUM_NULL = {"type": ["number", "null"]}
_BOOL = {"type": "boolean"}
_ARR = {"type": "array"}
_ARR_NULL = {"type": ["array", "null"]}
_OBJ = {"type": "object", "additionalProperties": True}

#: Shared pagination keys for list payloads (offset-based).
_PAGE = {
    "total": _INT_NULL,
    "returned": _INT,
    "limit": _INT,
    "offset": _INT,
    "truncated": _BOOL,
    "next_offset": _INT_NULL,
}

CAPABILITIES_SCHEMA = _envelope(
    server=_STR,
    server_version=_STR,
    capabilities_version=_STR,
    data_source=_STR,
    tools=_ARR,
    response_modes=_ARR,
    error_codes=_ARR,
    limits=_OBJ,
)

DIAGNOSTICS_SCHEMA = _envelope(
    base_url=_STR,
    api_reachable=_BOOL,
    api_name=_STR_NULL,
    api_version=_STR_NULL,
    error=_STR,
    build=_OBJ,
    runtime=_OBJ,
)

SEARCH_SCORE_SETS_SCHEMA = _envelope(
    query=_STR_NULL,
    results=_ARR,
    **_PAGE,
)

SCORE_SET_SCHEMA = _envelope(
    urn=_STR,
    title=_STR_NULL,
    short_description=_STR_NULL,
    num_variants=_INT_NULL,
    license=_STR_NULL,
    targets=_ARR,
    experiment_urn=_STR_NULL,
    publications=_OBJ,
    # MaveDB's curated interpretation layer: per-bin functional-class thresholds,
    # ACMG criterion + evidence strength, OddsPath, baseline (WT) anchor.
    score_calibrations=_ARR,
    record_url=_STR_NULL,
)

VARIANT_SCORES_SCHEMA = _envelope(
    urn=_STR,
    columns=_ARR,
    rows=_ARR,  # each row may carry a derived `classification` (primary calibration)
    calibrations=_ARR,  # thresholds block so the score column is interpretable
    returned=_INT,
    start=_INT,
    offset=_INT,
    limit=_INT,
    total=_INT_NULL,
    truncated=_BOOL,
    next_start=_INT_NULL,
    next_offset=_INT_NULL,
)

VARIANT_SCORE_SCHEMA = _envelope(
    # variant-URN direct-fetch form
    variant_urn=_STR_NULL,
    score_set_urn=_STR_NULL,
    hgvs_nt=_STR_NULL,
    hgvs_pro=_STR_NULL,
    score=_NUM_NULL,
    classifications=_ARR,  # per-calibration functional class (ACMG/OddsPath)
    # hgvs-scan form
    urn=_STR,
    query_hgvs=_STR_NULL,
    columns=_ARR,
    matches=_ARR,
    calibrations=_ARR,
    match_count=_INT,
    scanned_rows=_INT,
)

GENE_SCORE_SETS_SCHEMA = _envelope(
    gene=_OBJ,
    total_scored_variants=_INT_NULL,
    score_sets=_ARR,
    coverage=_OBJ,
    **_PAGE,
)

EXPERIMENT_SCHEMA = _envelope(
    urn=_STR,
    title=_STR_NULL,
    short_description=_STR_NULL,
    experiment_set_urn=_STR_NULL,
    score_set_urns=_ARR_NULL,
    num_score_sets=_INT_NULL,
    keywords=_ARR,
    publications=_OBJ,
    record_url=_STR_NULL,
)

SEARCH_EXPERIMENTS_SCHEMA = _envelope(
    query=_STR_NULL,
    results=_ARR,
    **_PAGE,
)

MAPPED_VARIANTS_SCHEMA = _envelope(
    urn=_STR,
    mapped_variants=_ARR,
    current_only=_BOOL,
    ordering=_STR,
    **_PAGE,
)

COLLECTION_SCHEMA = _envelope(
    urn=_STR,
    name=_STR_NULL,
    description=_STR_NULL,
    experiment_urns=_ARR_NULL,
    score_set_urns=_ARR_NULL,
)

FIND_VARIANT_SCHEMA = _envelope(
    vrs_id=_STR,
    hits=_ARR,  # each: {score_set_urn, variant_urn, vrs_id, clingen_allele_id, score?, classifications?}
    enriched=_BOOL,
    **_PAGE,
)

HGVS_VALIDATION_SCHEMA = _envelope(
    variant=_STR_NULL,
    valid=_BOOL,
    # `message` carries the upstream reason on valid=False (declared on the envelope).
)

CLASSIFIED_VARIANTS_SCHEMA = _envelope(
    urn=_STR,
    calibration_urn=_STR_NULL,
    calibration_title=_STR_NULL,
    classification=_STR_NULL,
    variants=_ARR,
    **_PAGE,
)

SCORE_DISTRIBUTION_SCHEMA = _envelope(
    urn=_STR,
    n=_INT,
    total_variants=_INT_NULL,
    truncated=_BOOL,
    min=_NUM_NULL,
    max=_NUM_NULL,
    mean=_NUM_NULL,
    median=_NUM_NULL,
    q1=_NUM_NULL,
    q3=_NUM_NULL,
    stdev=_NUM_NULL,
    histogram=_ARR,
    calibrations=_ARR,
    query=_OBJ,  # {score, percentile, classifications?} when a query score is given
)
