"""Single-variant lookup: resolve ONE variant's score (+ interpretation).

``get_variant_score`` has two entry forms that now return the **same shape** (F2):
a full variant URN, or a score-set URN + ``hgvs``. Both yield a top-level
``{urn, query, resolved_by, variants[], match_count, calibrations?}`` whose
``variants[]`` elements carry identical core keys at every ``response_mode``.
Embedded ``mapped_variants`` is opt-in (standard/full) and current-only unless
full -- the old by-URN payload leaked superseded ``current:false`` rows while the
by-hgvs payload was a different ``{matches}`` shape entirely.

Free functions over a :class:`MaveDBClient` (data plane): return plain dicts,
raise typed exceptions. Live here so ``mavedb_service`` stays within budget.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mavedb_link.api.client import MaveDBClient
from mavedb_link.constants import VARIANT_SCAN_LIMIT
from mavedb_link.exceptions import InvalidInputError, NotFoundError
from mavedb_link.identifiers import (
    is_variant_urn,
    score_set_urn_of_variant,
    validate_score_set_urn,
)
from mavedb_link.services.calibration import classify_score, shape_calibrations
from mavedb_link.services.scores import hgvs_matches, shape_scores
from mavedb_link.services.shaping import shape_single_variant

_FULL_MODES = ("standard", "full")


async def get_variant_score(
    client: MaveDBClient,
    urn: str,
    *,
    hgvs: str | None = None,
    response_mode: str = "compact",
) -> dict[str, Any]:
    """Look up ONE variant's score without paging the whole table (DEF-6, F2).

    A full variant URN (``…-a-1#<index>``) resolves directly; a score-set URN plus
    ``hgvs`` scans the score table once. Both paths return the unified shape.
    """
    candidate = urn.strip()
    if is_variant_urn(candidate):
        return await _resolve_by_urn(client, candidate, response_mode)
    score_set_urn = validate_score_set_urn(candidate)
    if not hgvs or not hgvs.strip():
        raise InvalidInputError(
            "Provide hgvs= (e.g. 'c.8168A>G' or 'p.Arg1699Trp') to look up one "
            "variant, or pass a full variant URN ('urn:mavedb:...-a-1#<index>').",
            field="hgvs",
            hint="Variant URNs are the 'accession'/'variant_urn' of get_variant_scores "
            "and get_mapped_variants.",
        )
    return await _resolve_by_hgvs(client, score_set_urn, hgvs.strip(), response_mode)


async def _raw_calibrations(
    client: MaveDBClient, score_set_urn: str | None
) -> list[dict[str, Any]]:
    """Best-effort fetch of a score set's raw ``scoreCalibrations`` (never raises).

    Classification enrichment must never fail the underlying score lookup, so any
    upstream error degrades to "no calibrations". The score-set read is cached.
    """
    if not score_set_urn:
        return []
    try:
        record = await client.get_json(f"/score-sets/{score_set_urn}")
    except Exception:  # best-effort: a calibration miss must not fail the lookup
        return []
    cals = record.get("scoreCalibrations") if isinstance(record, dict) else None
    return cals if isinstance(cals, list) else []


def _classified(view: dict[str, Any], calibrations: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach per-calibration ``classifications`` to a variant view (in place)."""
    classes = classify_score(view.get("score"), calibrations)
    if classes:
        view["classifications"] = classes
    return view


def _view_from_row(
    row: dict[str, Any], score_set_urn: str, calibrations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build a variant view from a parsed scores-CSV row (compact/minimal paths)."""
    view = {
        k: v
        for k, v in {
            "variant_urn": row.get("accession"),
            "variant_index": row.get("variant_index"),
            "score_set_urn": score_set_urn,
            "hgvs_nt": row.get("hgvs_nt"),
            "hgvs_pro": row.get("hgvs_pro"),
            "score": row.get("score"),
        }.items()
        if v is not None
    }
    return _classified(view, calibrations)


async def _view_from_record(
    client: MaveDBClient,
    variant_urn: str,
    response_mode: str,
    calibrations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Fetch a variant record and shape it (carries mapped_variants at standard/full)."""
    # The '#<index>' must be percent-encoded or httpx drops it as a URL fragment.
    try:
        raw = await client.get_json(f"/variants/{variant_urn.replace('#', '%23')}")
    except Exception:  # best-effort: fall back to the row view
        return None
    return _classified(shape_single_variant(raw, response_mode), calibrations)


async def _resolve_by_urn(
    client: MaveDBClient, variant_urn: str, response_mode: str
) -> dict[str, Any]:
    """Resolve a full variant URN to the unified shape."""
    raw = await client.get_json(f"/variants/{variant_urn.replace('#', '%23')}")
    view = shape_single_variant(raw, response_mode)
    score_set_urn = str(
        view.get("score_set_urn") or score_set_urn_of_variant(variant_urn) or variant_urn
    )
    calibrations = await _raw_calibrations(client, score_set_urn)
    _classified(view, calibrations)
    return _wrap(score_set_urn, variant_urn, "variant_urn", [view], calibrations, response_mode)


async def _resolve_by_hgvs(
    client: MaveDBClient, score_set_urn: str, hgvs: str, response_mode: str
) -> dict[str, Any]:
    """Resolve a score-set URN + hgvs to the unified shape (table scan)."""
    text = await client.get_text(
        f"/score-sets/{score_set_urn}/scores", params={"start": 0, "limit": VARIANT_SCAN_LIMIT}
    )
    rows = shape_scores(text, start=0, limit=VARIANT_SCAN_LIMIT)["rows"]
    query = hgvs.lower()
    matched = [r for r in rows if hgvs_matches(r, query)]
    if not matched:
        protein = query.startswith("p.") or ":p." in query
        hint = (
            "Many SGE sets leave hgvs_pro null, so a p. form cannot be matched there "
            "-- try the c. (nucleotide) form instead."
            if protein
            else "Matching is accession-prefix-insensitive (bare 'c.8168A>G' resolves "
            "'ENST...:c.8168A>G'); confirm the variant exists via get_variant_scores "
            "or validate the string with get_hgvs_validation."
        )
        raise NotFoundError(
            f"No variant matching hgvs '{hgvs}' in {score_set_urn} (scanned {len(rows)} "
            f"rows). {hint} Or pass a full variant URN ('{score_set_urn}#<index>')."
        )
    calibrations = await _raw_calibrations(client, score_set_urn)
    if response_mode in _FULL_MODES:
        # Fetch each matched variant's record so the view (mapped_variants,
        # count_data) is byte-for-byte identical to the by-URN path.
        records = await asyncio.gather(
            *(
                _view_from_record(client, r["accession"], response_mode, calibrations)
                for r in matched
                if isinstance(r.get("accession"), str)
            )
        )
        views = [
            rec if rec is not None else _view_from_row(row, score_set_urn, calibrations)
            for rec, row in zip(records, matched, strict=False)
        ]
    else:
        views = [_view_from_row(r, score_set_urn, calibrations) for r in matched]
    return _wrap(score_set_urn, hgvs, "hgvs", views, calibrations, response_mode)


def _wrap(
    score_set_urn: str,
    query: str,
    resolved_by: str,
    views: list[dict[str, Any]],
    calibrations: list[dict[str, Any]],
    response_mode: str,
) -> dict[str, Any]:
    """Assemble the unified top-level payload shared by both resolution paths."""
    payload: dict[str, Any] = {
        "urn": score_set_urn,
        "query": query,
        "resolved_by": resolved_by,
        "variants": views,
        "match_count": len(views),
    }
    if calibrations:
        payload["calibrations"] = shape_calibrations(
            calibrations, full=response_mode in _FULL_MODES
        )
    return payload
