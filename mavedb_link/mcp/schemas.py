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

#: A v1.1 untrusted_text fenced object (Response-Envelope Standard v1.1): externally
#: sourced depositor prose typed as data with a ``kind`` literal, so hosts and the
#: router treat the subtree opaque (never as instructions). Nullable because
#: ``response_mode`` may omit the field for a given record.
_UNTRUSTED_TEXT_NULL = {
    "type": ["object", "null"],
    "additionalProperties": True,
    "properties": {
        "kind": {"const": "untrusted_text"},
        "text": _STR,
        "provenance": _OBJ,
        "raw_sha256": _STR,
    },
}

#: Discovery list-item schemas. Declared so the fenced free-text fields carry the
#: ``kind`` literal for list rows too, not just the single-record tools. Permissive
#: (``additionalProperties: true``) because ``response_mode`` projects other fields.
_SCORE_SET_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "urn": _STR,
        "short_description": _UNTRUSTED_TEXT_NULL,
        "abstract_text": _UNTRUSTED_TEXT_NULL,
        "method_text": _UNTRUSTED_TEXT_NULL,
    },
}
_EXPERIMENT_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "urn": _STR,
        "short_description": _UNTRUSTED_TEXT_NULL,
        "abstract_text": _UNTRUSTED_TEXT_NULL,
        "method_text": _UNTRUSTED_TEXT_NULL,
    },
}
_SCORE_SET_ARR = {"type": "array", "items": _SCORE_SET_ITEM}
_EXPERIMENT_ARR = {"type": "array", "items": _EXPERIMENT_ITEM}

#: Calibration (thresholds ladder) item. Its full-mode ``baseline_score_description``
#: and ``notes`` are externally sourced prose, fenced as v1.1 untrusted_text.
_CALIBRATION_ITEM = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "baseline_score_description": _UNTRUSTED_TEXT_NULL,
        "notes": _UNTRUSTED_TEXT_NULL,
    },
}
_CALIBRATION_ARR = {"type": "array", "items": _CALIBRATION_ITEM}

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
    interpretation=_OBJ,  # A4: calibration support + surfacing tools
)

SEARCH_SCORE_SETS_SCHEMA = _envelope(
    query=_STR_NULL,
    results=_SCORE_SET_ARR,
    **_PAGE,
)

SCORE_SET_SCHEMA = _envelope(
    urn=_STR,
    title=_STR_NULL,
    # Depositor prose, fenced as v1.1 untrusted_text (typed data, never instructions).
    short_description=_UNTRUSTED_TEXT_NULL,
    abstract_text=_UNTRUSTED_TEXT_NULL,
    method_text=_UNTRUSTED_TEXT_NULL,
    num_variants=_INT_NULL,
    license=_STR_NULL,
    targets=_ARR,
    experiment_urn=_STR_NULL,
    publications=_OBJ,
    # MaveDB's curated interpretation layer: per-bin functional-class thresholds,
    # ACMG criterion + evidence strength, OddsPath, baseline (WT) anchor.
    score_calibrations=_CALIBRATION_ARR,
    record_url=_STR_NULL,
)

VARIANT_SCORES_SCHEMA = _envelope(
    urn=_STR,
    columns=_ARR,
    rows=_ARR,  # each row may carry a derived `classification` (primary calibration)
    calibrations=_CALIBRATION_ARR,  # thresholds block so the score column is interpretable
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
    # Unified shape for BOTH resolution paths (variant URN and score-set + hgvs):
    # the same key set at every response_mode (F2).
    urn=_STR,  # the score-set URN
    query=_STR_NULL,  # the variant URN or hgvs string that was resolved
    resolved_by=_STR,  # "variant_urn" | "hgvs"
    match_count=_INT,
    # each variant: {variant_urn, variant_index, score_set_urn, hgvs_nt, hgvs_pro,
    # score, classifications?, (standard/full) score_data/count_data/mapped_variants}
    variants=_ARR,
    calibrations=_CALIBRATION_ARR,  # thresholds block, present when the set is calibrated
)

GENE_SCORE_SETS_SCHEMA = _envelope(
    gene=_OBJ,
    total_scored_variants=_INT_NULL,
    score_sets=_SCORE_SET_ARR,
    coverage=_OBJ,
    **_PAGE,
)

EXPERIMENT_SCHEMA = _envelope(
    urn=_STR,
    title=_STR_NULL,
    # Depositor prose, fenced as v1.1 untrusted_text (typed data, never instructions).
    short_description=_UNTRUSTED_TEXT_NULL,
    abstract_text=_UNTRUSTED_TEXT_NULL,
    method_text=_UNTRUSTED_TEXT_NULL,
    experiment_set_urn=_STR_NULL,
    score_set_urns=_ARR_NULL,
    num_score_sets=_INT_NULL,
    keywords=_ARR,
    publications=_OBJ,
    record_url=_STR_NULL,
)

SEARCH_EXPERIMENTS_SCHEMA = _envelope(
    query=_STR_NULL,
    results=_EXPERIMENT_ARR,
    **_PAGE,
)

MAPPED_VARIANTS_SCHEMA = _envelope(
    urn=_STR,
    # each row carries variant_index (numeric #index) so callers join with
    # get_variant_scores rows by value, never by fragile position (F1).
    mapped_variants=_ARR,
    current_only=_BOOL,
    ordering=_STR,
    join_key=_STR,
    **_PAGE,
)

COLLECTION_SCHEMA = _envelope(
    urn=_STR,
    name=_STR_NULL,
    # Curator collection description, fenced as v1.1 untrusted_text.
    description=_UNTRUSTED_TEXT_NULL,
    num_experiments=_INT,
    num_score_sets=_INT,
    experiment_urns=_ARR_NULL,
    score_set_urns=_ARR_NULL,  # paged window of the member datasets (F12)
    **_PAGE,
)

FIND_VARIANT_SCHEMA = _envelope(
    vrs_id=_STR,  # the resolved GA4GH allele id (first, when several resolved)
    resolved_vrs=_ARR,  # all resolved GA4GH allele ids (>=1)
    resolved_by=_STR,  # "vrs_id" | "variant_urn" | "hgvs"
    hgvs_input=_STR_NULL,  # the HGVS string that was resolved (hgvs path only)
    probe_truncated=_BOOL,  # live-probe hit its score-set cap (hgvs path only)
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
    calibrations=_CALIBRATION_ARR,
    query=_OBJ,  # {score, percentile, classifications?} when a query score is given
)
