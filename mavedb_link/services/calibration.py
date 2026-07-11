"""Functional-classification calibration: shape + classify (data-plane, pure).

MaveDB curates the decision-relevant interpretation layer for a score set in
``scoreCalibrations`` (dropped by the MCP until now): per-bin functional-class
thresholds, ACMG criteria (PS3/BS3) with evidence strength, OddsPath ratios, and
the baseline (wild-type) anchor. This module normalises that block to tidy
snake_case and maps a numeric score to its functional class.

The classifier is deliberately **range-driven, direction-agnostic, and
gap-aware**: it never assumes higher = normal (MaveDB sets go both ways), and a
score landing in no bin returns ``indeterminate`` rather than snapping to the
nearest class. Multiple calibrations yield one result each.

Pure functions over upstream dicts so they unit-test in isolation.
"""

from __future__ import annotations

import math
from typing import Any

from mavedb_link.mcp.untrusted_content import fence_prose

#: The functional class assigned when a score falls in no calibrated bin.
INDETERMINATE = "indeterminate"

#: Significant figures retained for emitted calibration thresholds/ratios/baselines.
#: MaveDB serialises bin edges at full double precision (e.g. -0.9092407272057206);
#: those trailing digits are measurement noise no consumer needs, so the *output*
#: is rounded to this many sig figs. Range MATCHING still uses the raw thresholds.
CALIBRATION_SIG_FIGS = 6


def round_sig(value: Any, sig: int = CALIBRATION_SIG_FIGS) -> Any:
    """Round a float to ``sig`` significant figures; pass non-floats through.

    Trims double-precision noise from calibration thresholds without touching ints,
    ``None``, bools, or non-finite values, and guarantees a short JSON repr (via
    ``%g``). Never raises. Only the *displayed* numbers are rounded — the classifier
    compares against the raw, unrounded thresholds so bin membership is unchanged.
    """
    if isinstance(value, bool) or not isinstance(value, float):
        return value
    if not math.isfinite(value):
        return value
    return float(f"{value:.{sig}g}")


def _round_range(bounds: Any) -> Any:
    """Round each finite bound of a ``[lower, upper]`` range (``None`` preserved)."""
    if not isinstance(bounds, list):
        return bounds
    return [round_sig(b) for b in bounds]


def coerce_score(value: Any) -> float | None:
    """Coerce a score to ``float``, or ``None`` if it cannot be (never raises).

    The scores-CSV path coerces numeric cells to float, but the variant-record
    path (``GET /variants/{urn}``) can serialise ``score`` as a *string* for some
    sets (verified live on urn:mavedb:00001242-a-1). Routing every score through
    this coercion keeps the range comparisons numeric so the classifier never
    crashes on ``str <= float`` (GAP-2). A non-numeric token yields ``None``.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a score
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _drop_none(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with ``None`` values removed."""
    return {k: v for k, v in payload.items() if v is not None}


def _in_range(score: float, classification: dict[str, Any]) -> bool:
    """Whether ``score`` lies in a functional classification's ``range``.

    ``range`` is ``[lower, upper]`` with either bound ``None`` (unbounded);
    ``inclusiveLowerBound`` / ``inclusiveUpperBound`` toggle boundary inclusion.
    """
    bounds = classification.get("range") or [None, None]
    lower, upper = bounds[0], bounds[1]
    if lower is not None:
        if classification.get("inclusiveLowerBound"):
            if score < lower:
                return False
        elif score <= lower:
            return False
    if upper is not None:
        if classification.get("inclusiveUpperBound"):
            if score > upper:
                return False
        elif score >= upper:
            return False
    return True


def _match_classification(score: float, calibration: dict[str, Any]) -> dict[str, Any] | None:
    """Return the functional classification containing ``score`` (or ``None``)."""
    classifications: list[dict[str, Any]] = calibration.get("functionalClassifications") or []
    for fc in classifications:
        if _in_range(score, fc):
            return fc
    return None


def _acmg(fc: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Pull ``(criterion, evidence_strength)`` from a classification's ACMG block."""
    acmg = (fc or {}).get("acmgClassification") or {}
    return acmg.get("criterion"), acmg.get("evidenceStrength")


def classify_score(
    score: float | str | None, calibrations: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Classify ``score`` under each calibration (one result entry per calibration).

    Returns ``[]`` when there are no calibrations or ``score`` is missing/
    non-numeric. A score in no bin yields ``classification="indeterminate"``.
    """
    numeric = coerce_score(score)
    if numeric is None or not calibrations:
        return []
    score = numeric
    results: list[dict[str, Any]] = []
    for calibration in calibrations:
        fc = _match_classification(score, calibration)
        criterion, strength = _acmg(fc)
        results.append(
            _drop_none(
                {
                    "calibration": calibration.get("title"),
                    "classification": (fc.get("functionalClassification") if fc else INDETERMINATE),
                    "label": fc.get("label") if fc else None,
                    "acmg": criterion,
                    "acmg_strength": strength,
                    "oddspath": round_sig(fc.get("oddspathsRatio")) if fc else None,
                    "baseline_score": round_sig(calibration.get("baselineScore")),
                }
            )
        )
    return results


def _primary_calibration(calibrations: list[dict[str, Any]]) -> dict[str, Any]:
    """The calibration flagged ``primary`` (else the first)."""
    for calibration in calibrations:
        if calibration.get("primary"):
            return calibration
    return calibrations[0]


def primary_classification(
    score: float | str | None, calibrations: list[dict[str, Any]] | None
) -> str | None:
    """The single functional class from the primary calibration (for per-row tags).

    ``None`` when there are no calibrations or ``score`` is missing/non-numeric.
    """
    numeric = coerce_score(score)
    if numeric is None or not calibrations:
        return None
    score = numeric
    calibration = _primary_calibration(calibrations)
    fc = _match_classification(score, calibration)
    return fc.get("functionalClassification") if fc else INDETERMINATE


def _shape_threshold_source(source: dict[str, Any]) -> dict[str, Any]:
    """Compact a threshold-source publication to ``{db_name, identifier, title}``."""
    return _drop_none(
        {
            "db_name": source.get("dbName"),
            "identifier": source.get("identifier"),
            "title": source.get("title"),
        }
    )


def _shape_classification(fc: dict[str, Any], *, full: bool) -> dict[str, Any]:
    """Normalise one functional-classification bin to tidy snake_case."""
    criterion, strength = _acmg(fc)
    shaped = {
        "label": fc.get("label"),
        "classification": fc.get("functionalClassification"),
        "range": _round_range(fc.get("range")),
        "inclusive_lower": fc.get("inclusiveLowerBound"),
        "inclusive_upper": fc.get("inclusiveUpperBound"),
        "acmg": criterion,
        "acmg_strength": strength,
        "oddspath": round_sig(fc.get("oddspathsRatio")),
        "variant_count": fc.get("variantCount"),
    }
    if full:
        shaped["id"] = fc.get("id")
    return _drop_none(shaped)


def shape_calibrations(
    calibrations: list[dict[str, Any]] | None, *, full: bool, record_id_base: str = ""
) -> list[dict[str, Any]]:
    """Normalise the ``scoreCalibrations`` block to tidy snake_case.

    ``full`` adds the bin ``id`` and the calibration ``notes`` /
    ``baseline_score_description``; threshold sources are always compacted to
    ``{db_name, identifier, title}`` (the heavy author lists are dropped).

    ``notes`` and ``baseline_score_description`` are externally sourced depositor
    prose, so they are fenced as v1.1 ``untrusted_text`` objects (typed data, never
    instructions). ``record_id_base`` is the parent score-set/variant URN used to
    stamp their provenance ``record_id``.
    """
    out: list[dict[str, Any]] = []
    for calibration in calibrations or []:
        shaped = {
            "title": calibration.get("title"),
            "primary": calibration.get("primary"),
            "research_use_only": calibration.get("researchUseOnly"),
            "baseline_score": round_sig(calibration.get("baselineScore")),
            "classifications": [
                _shape_classification(fc, full=full)
                for fc in calibration.get("functionalClassifications") or []
            ],
            "threshold_sources": [
                _shape_threshold_source(s) for s in calibration.get("thresholdSources") or []
            ],
        }
        if full:
            shaped["baseline_score_description"] = fence_prose(
                calibration.get("baselineScoreDescription"),
                source="mavedb",
                record_id=f"{record_id_base}#baselineScoreDescription",
            )
            shaped["notes"] = fence_prose(
                calibration.get("notes"),
                source="mavedb",
                record_id=f"{record_id_base}#notes",
            )
            shaped["urn"] = calibration.get("urn")
        out.append(_drop_none(shaped))
    return out
