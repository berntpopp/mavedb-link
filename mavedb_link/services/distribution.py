"""get_score_distribution: server-side summary statistics over a scores table.

MaveDB has no per-score-set distribution endpoint, so this is genuine MCP
value-add: read the scores CSV once and return a compact summary (n, min/max,
mean/median, quartiles, stdev, histogram) instead of every row -- and, given a
query ``score``, its percentile + calibrated classification. The summary is the
honest answer to the token blow-up of paging tens of thousands of rows.

A free function over a :class:`MaveDBClient` (data plane): returns a plain dict,
raises typed exceptions. Lives here so ``mavedb_service`` stays within budget.
"""

from __future__ import annotations

import asyncio
import statistics
from bisect import bisect_right
from typing import Any

from mavedb_link.api.client import MaveDBClient
from mavedb_link.constants import DISTRIBUTION_BINS, DISTRIBUTION_FETCH_LIMIT
from mavedb_link.exceptions import NotFoundError
from mavedb_link.identifiers import validate_score_set_urn
from mavedb_link.services.calibration import classify_score, shape_calibrations
from mavedb_link.services.scores import parse_scores_csv


def _summary(values: list[float]) -> dict[str, Any]:
    """Five-number-ish summary of a sorted, non-empty value list."""
    out: dict[str, Any] = {
        "min": values[0],
        "max": values[-1],
        "mean": round(statistics.fmean(values), 6),
        "median": round(statistics.median(values), 6),
    }
    if len(values) >= 2:
        out["stdev"] = round(statistics.stdev(values), 6)
        q1, _q2, q3 = statistics.quantiles(values, n=4)
        out["q1"] = round(q1, 6)
        out["q3"] = round(q3, 6)
    return out


def _histogram(values: list[float], *, bins: int) -> list[dict[str, Any]]:
    """Bin a sorted value list into ``bins`` equal-width buckets."""
    lo, hi = values[0], values[-1]
    if hi == lo:
        return [{"start": lo, "end": hi, "count": len(values)}]
    width = (hi - lo) / bins
    counts = [0] * bins
    for value in values:
        idx = min(int((value - lo) / width), bins - 1)
        counts[idx] += 1
    return [
        {"start": round(lo + i * width, 6), "end": round(lo + (i + 1) * width, 6), "count": count}
        for i, count in enumerate(counts)
    ]


async def score_distribution(
    client: MaveDBClient, urn: str, *, score: float | None = None
) -> dict[str, Any]:
    """Summarise a score set's score column (+ a query score's percentile/class)."""
    score_set_urn = validate_score_set_urn(urn)
    gathered: Any = await asyncio.gather(
        client.get_text(
            f"/score-sets/{score_set_urn}/scores",
            params={"start": 0, "limit": DISTRIBUTION_FETCH_LIMIT},
        ),
        client.get_json(f"/score-sets/{score_set_urn}"),
        return_exceptions=True,
    )
    text, record = gathered[0], gathered[1]
    if isinstance(text, BaseException):
        raise text
    _columns, rows = parse_scores_csv(text)
    values = sorted(r["score"] for r in rows if isinstance(r.get("score"), (int, float)))
    if not values:
        raise NotFoundError(
            f"No numeric scores to summarise in {score_set_urn} (scanned {len(rows)} rows)."
        )
    raw_calibrations = record.get("scoreCalibrations") if isinstance(record, dict) else None
    total_variants = record.get("numVariants") if isinstance(record, dict) else None
    payload: dict[str, Any] = {
        "urn": score_set_urn,
        "n": len(values),
        "total_variants": total_variants,
        "truncated": len(rows) >= DISTRIBUTION_FETCH_LIMIT,
        **_summary(values),
        "histogram": _histogram(values, bins=DISTRIBUTION_BINS),
    }
    if isinstance(raw_calibrations, list) and raw_calibrations:
        payload["calibrations"] = shape_calibrations(raw_calibrations, full=False)
    if score is not None:
        query: dict[str, Any] = {
            "score": score,
            "percentile": round(100.0 * bisect_right(values, score) / len(values), 2),
        }
        classified = classify_score(
            score, raw_calibrations if isinstance(raw_calibrations, list) else []
        )
        if classified:
            query["classifications"] = classified
        payload["query"] = query
    return payload
