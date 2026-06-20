"""P1 resolver operations: cross-dataset VRS lookup, HGVS validation, variant-by-class.

Free functions over a :class:`MaveDBClient` (data plane): each returns a plain
dict and raises a typed exception, never an MCP envelope. They live here rather
than on ``MaveDBService`` to keep that module within the 600-LOC budget; thin
``MaveDBService`` methods delegate to them.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from mavedb_link.api.client import MaveDBClient
from mavedb_link.constants import (
    DEFAULT_CLASSIFIED_LIMIT,
    DEFAULT_FIND_LIMIT,
    FUNCTIONAL_CLASSES,
    MAX_CLASSIFIED_LIMIT,
    MAX_FIND_LIMIT,
)
from mavedb_link.exceptions import InvalidInputError, NotFoundError
from mavedb_link.identifiers import (
    is_variant_urn,
    score_set_urn_of_variant,
    validate_score_set_urn,
    variant_index_of,
)
from mavedb_link.services.calibration import classify_score, coerce_score
from mavedb_link.services.shaping import shape_mapped_variant, shape_single_variant

#: GA4GH VRS allele ids start with this scheme; the upstream endpoint enforces it.
_VRS_PREFIX = "ga4gh:"

#: Process-wide memo of HGVS validation results. Upstream /hgvs/validate is
#: idempotent (a string is valid-or-not regardless of when asked), so a repeat is
#: served without the ~1.6s round-trip (D.2). Bounded FIFO; only settled results
#: (valid / parseable-but-wrong) are stored -- transient errors raise before here.
_HGVS_CACHE: dict[str, dict[str, Any]] = {}
_HGVS_CACHE_MAX = 2048


def clear_hgvs_validation_cache() -> None:
    """Drop the HGVS validation memo (used for test isolation)."""
    _HGVS_CACHE.clear()


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp ``value`` into ``[lo, hi]``."""
    return max(lo, min(value, hi))


def _page_block(*, total: int, returned: int, limit: int, offset: int) -> dict[str, Any]:
    """Build the uniform offset-based pagination block for a list payload."""
    truncated = offset + returned < total
    return {
        "total": total,
        "returned": returned,
        "limit": limit,
        "offset": offset,
        "truncated": truncated,
        "next_offset": offset + returned if truncated else None,
    }


async def _enrich_hit(client: MaveDBClient, hit: dict[str, Any]) -> None:
    """Attach a cross-dataset hit's ``score`` + ``classifications`` in place.

    Best-effort: an enrichment failure leaves the hit's identity intact.
    """
    variant_urn = hit.get("variant_urn")
    if not variant_urn:
        return
    try:
        raw = await client.get_json(f"/variants/{variant_urn.replace('#', '%23')}")
    except Exception:  # best-effort: enrichment must not fail the lookup
        return
    shaped = shape_single_variant(raw, "compact")
    hit["score"] = shaped.get("score")
    set_urn = shaped.get("score_set_urn")
    calibrations: list[dict[str, Any]] = []
    if set_urn:
        try:
            record = await client.get_json(f"/score-sets/{set_urn}")
        except Exception:  # best-effort
            record = None
        cals = record.get("scoreCalibrations") if isinstance(record, dict) else None
        if isinstance(cals, list):
            calibrations = cals
    classified = classify_score(shaped.get("score"), calibrations)
    if classified:
        hit["classifications"] = classified


async def _vrs_from_variant(client: MaveDBClient, variant_urn: str) -> str:
    """Resolve a variant URN to its genome-mapped VRS allele id (2.2 consolidation).

    The variant record carries its own ``mappedVariants``, so a caller who has a
    variant (by URN, or via get_variant_score) need not map it first to fan out
    cross-dataset. A variant with no mapping cannot be matched -> ``NotFoundError``.
    """
    candidate = variant_urn.strip()
    if not is_variant_urn(candidate):
        raise InvalidInputError(
            "variant_urn must be a full variant URN ('urn:mavedb:00000001-a-1#2').",
            field="variant_urn",
            hint="It is the 'variant_urn'/'accession' from get_variant_scores or "
            "get_variant_score.",
        )
    # Mirror fast-path (D.3): the annotation index maps the variant URN -> VRS
    # directly, so the common find_variant(variant_urn=) path skips a live variant
    # fetch. Duck-typed so a plain live client just falls through to the record read.
    from_mirror = getattr(client, "mapped_vrs_for_variant", None)
    if callable(from_mirror):
        mirror_vrs = from_mirror(candidate)
        if mirror_vrs:
            return str(mirror_vrs)
    raw = await client.get_json(f"/variants/{candidate.replace('#', '%23')}")
    shaped = shape_single_variant(raw, "standard")  # standard carries current mappings
    for mapping in shaped.get("mapped_variants") or []:
        vrs = mapping.get("vrs_id")
        if vrs:
            return str(vrs)
    raise NotFoundError(
        f"{candidate} has no genome-mapped VRS allele, so it cannot be matched across "
        "score sets. It may be unmapped upstream — call get_mapped_variants on its "
        "score set to confirm."
    )


async def _resolve_cross_dataset_ident(
    client: MaveDBClient, vrs_id: str | None, variant_urn: str | None
) -> tuple[str, str]:
    """Return ``(vrs_allele_id, resolved_by)`` from a VRS id OR a variant URN.

    A variant URN (explicit, or auto-detected when passed as ``vrs_id``) is
    resolved to its VRS internally; a ClinGen Allele ID is rejected with a remedy.
    """
    if variant_urn and variant_urn.strip():
        return await _vrs_from_variant(client, variant_urn), "variant_urn"
    candidate = (vrs_id or "").strip()
    if not candidate:
        raise InvalidInputError(
            "Provide vrs_id (a GA4GH 'ga4gh:' allele id) or variant_urn ('urn:mavedb:...-a-1#2').",
            field="vrs_id",
            hint="Get a VRS id from get_mapped_variants/get_variant_score, or pass a "
            "variant_urn to resolve it internally.",
        )
    if candidate.startswith(_VRS_PREFIX):
        return candidate, "vrs_id"
    if is_variant_urn(candidate):  # friendly: a variant URN where a VRS id would go
        return await _vrs_from_variant(client, candidate), "variant_urn"
    raise InvalidInputError(
        "find_variant needs a GA4GH VRS allele id (starts 'ga4gh:') or a variant URN.",
        field="vrs_id",
        hint="ClinGen Allele IDs are not accepted upstream — get the VRS via "
        "get_mapped_variants, or pass the variant_urn to resolve it internally.",
    )


async def find_variant(
    client: MaveDBClient,
    vrs_id: str | None = None,
    *,
    variant_urn: str | None = None,
    only_current: bool = True,
    enrich: bool = True,
    limit: int = DEFAULT_FIND_LIMIT,
    offset: int = 0,
    response_mode: str = "compact",
) -> dict[str, Any]:
    """Find one variant across every MaveDB score set (cross-dataset rollup).

    Accepts a GA4GH VRS allele id OR a ``variant_urn`` (resolved to its VRS via the
    variant record, so no map-first round-trip). Wraps ``GET /mapped-variants/vrs/
    {id}``: the same allele's measurements wherever it was assayed. With ``enrich``
    (default), each hit also carries the variant's ``score`` + calibrated
    ``classifications``.
    """
    ident, resolved_by = await _resolve_cross_dataset_ident(client, vrs_id, variant_urn)
    capped = _clamp(limit, 1, MAX_FIND_LIMIT)
    raw = await client.get_json(
        f"/mapped-variants/vrs/{quote(ident, safe='')}",
        params={"only_current": only_current},
    )
    items = raw if isinstance(raw, list) else (raw.get("mappedVariants") or [])
    items = sorted(items, key=_cross_dataset_sort_key)
    total = len(items)
    page = items[offset : offset + capped]
    hits: list[dict[str, Any]] = []
    for row in page:
        hit = shape_mapped_variant(row, response_mode)
        variant_urn_hit = hit.get("variant_urn")
        hit["score_set_urn"] = (
            score_set_urn_of_variant(variant_urn_hit) if variant_urn_hit else None
        )
        hits.append(hit)
    if enrich:
        await asyncio.gather(*(_enrich_hit(client, h) for h in hits))
    return {
        "vrs_id": ident,
        "resolved_by": resolved_by,
        "hits": hits,
        "enriched": enrich,
        **_page_block(total=total, returned=len(hits), limit=capped, offset=offset),
    }


def _mapped_variant_urn(item: Any) -> str:
    """A mapped-variant record's source variant URN (or empty)."""
    if not isinstance(item, dict):
        return ""
    return item.get("variantUrn") or (item.get("variant") or {}).get("urn") or ""


def _cross_dataset_sort_key(item: Any) -> tuple[str, int]:
    """Group cross-dataset hits by score set, then order numerically by index.

    A VRS allele can appear in several score sets; ordering by the score-set URN
    then the numeric variant index keeps a stable, human-sensible order (rather
    than the lexical #1,#10,#2 a string sort would give).
    """
    urn = _mapped_variant_urn(item)
    base = score_set_urn_of_variant(urn) or urn
    index = variant_index_of(urn)
    return (base, index if index is not None else 2**62)


async def get_hgvs_validation(client: MaveDBClient, variant: str) -> dict[str, Any]:
    """Validate an HGVS string via ``POST /hgvs/validate``.

    Returns ``{variant, valid, message}``. A valid string yields ``valid=True``;
    a parseable-but-wrong one yields ``valid=False`` with the upstream reason
    (e.g. reference-base disagreement, missing accession) so the caller can fix
    it before a lookup fails.
    """
    candidate = variant.strip()
    if not candidate:
        raise InvalidInputError(
            "Provide an HGVS string to validate.",
            field="variant",
            hint="e.g. 'NM_000059.4:c.8167G>A' or 'NP_000050.3:p.Asp2723His'.",
        )
    cached = _HGVS_CACHE.get(candidate)
    if cached is not None:
        return dict(cached)  # a copy so a caller cannot mutate the shared entry
    try:
        result = await client.post_json("/hgvs/validate", json={"variant": candidate})
    except InvalidInputError as exc:  # 400/422: invalid, surface the reason
        payload = {"variant": candidate, "valid": False, "message": exc.message}
    else:
        payload = {
            "variant": candidate,
            "valid": bool(result),
            "message": "Valid per MaveDB validation.",
        }
    if len(_HGVS_CACHE) >= _HGVS_CACHE_MAX:  # bounded FIFO: evict the oldest entry
        _HGVS_CACHE.pop(next(iter(_HGVS_CACHE)), None)
    _HGVS_CACHE[candidate] = payload
    return dict(payload)


def _shape_classified_variant(variant: dict[str, Any], fc: dict[str, Any]) -> dict[str, Any]:
    """Project one calibrated variant (+ its functional-class metadata)."""
    score = coerce_score(((variant.get("data") or {}).get("score_data") or {}).get("score"))
    acmg = fc.get("acmgClassification") or {}
    out: dict[str, Any] = {
        "variant_urn": variant.get("urn"),
        "hgvs_nt": variant.get("hgvsNt"),
        "hgvs_pro": variant.get("hgvsPro"),
        "score": score,
        "classification": fc.get("functionalClassification"),
        "label": fc.get("label"),
        "acmg": acmg.get("criterion"),
        "acmg_strength": acmg.get("evidenceStrength"),
    }
    return {k: v for k, v in out.items() if v is not None}


async def get_classified_variants(
    client: MaveDBClient,
    urn: str,
    *,
    classification: str | None = None,
    calibration_urn: str | None = None,
    limit: int = DEFAULT_CLASSIFIED_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a score set's variants in a calibrated functional class.

    Resolves the primary calibration (or ``calibration_urn``), then lists its
    variants from ``/score-calibrations/{urn}/variants`` (grouped by class id),
    optionally filtered to one ``classification`` (abnormal | normal |
    not_specified). Paged via offset/limit.
    """
    score_set_urn = validate_score_set_urn(urn)
    if classification is not None and classification not in FUNCTIONAL_CLASSES:
        raise InvalidInputError(
            f"Unknown classification '{classification}'.",
            field="classification",
            allowed=FUNCTIONAL_CLASSES,
        )
    capped = _clamp(limit, 1, MAX_CLASSIFIED_LIMIT)
    if calibration_urn:
        calibration = await client.get_json(f"/score-calibrations/{calibration_urn.strip()}")
    else:
        calibration = await client.get_json(
            f"/score-calibrations/score-set/{score_set_urn}/primary"
        )
    calib_urn = calibration.get("urn") if isinstance(calibration, dict) else None
    if not calib_urn:
        raise NotFoundError(
            f"No calibration for {score_set_urn}. Call get_score_set to confirm "
            "whether this score set carries any functional-classification thresholds."
        )
    id_to_class: dict[Any, dict[str, Any]] = {
        fc["id"]: fc
        for fc in calibration.get("functionalClassifications") or []
        if fc.get("id") is not None
    }
    groups = await client.get_json(f"/score-calibrations/{calib_urn}/variants")
    groups = groups if isinstance(groups, list) else [groups]
    variants: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        fc = id_to_class.get(group.get("functionalClassificationId"), {})
        if classification is not None and fc.get("functionalClassification") != classification:
            continue
        for variant in group.get("variants") or []:
            variants.append(_shape_classified_variant(variant, fc))
    variants.sort(key=lambda v: v.get("variant_urn") or "")
    total = len(variants)
    page = variants[offset : offset + capped]
    return {
        "urn": score_set_urn,
        "calibration_urn": calib_urn,
        "calibration_title": calibration.get("title"),
        "classification": classification,
        "variants": page,
        **_page_block(total=total, returned=len(page), limit=capped, offset=offset),
    }
