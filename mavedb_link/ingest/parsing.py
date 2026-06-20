"""Pure parsing helpers for the dump -> SQLite build (no I/O, no SQLite).

The dump's score/count CSVs use namespaced headers (``scores.score``,
``counts.c_0``, ``mavedb.post_mapped_vrs_digest``); the live ``/scores`` endpoint
and the existing parser use plain headers, so :func:`denamespace_csv` strips a
*leading known-namespace segment* only (live columns like ``exp.score`` keep
their dots). The annotations CSV is parsed into the cross-dataset mapped-variant
identity rows, and the score column drives the precomputed distribution.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from mavedb_link.constants import DISTRIBUTION_BINS
from mavedb_link.services.scores import hgvs_core, parse_scores_csv

#: Namespace prefixes the dump prepends to non-core columns (``include_post_mapped``
#: uses the ``mavedb`` namespace; score/count columns use ``scores``/``counts``).
_NAMESPACE_PREFIXES = ("scores.", "counts.", "mavedb.", "vep.", "gnomad.", "clingen.")

#: Percentile breakpoints stored per set (so percentile-of-score needs no scan).
_QUANTILE_POINTS = (1, 5, 10, 25, 50, 75, 90, 95, 99)


def denamespace_column(column: str) -> str:
    """Strip a single leading dump-namespace segment (``scores.exp.score`` -> ``exp.score``)."""
    for prefix in _NAMESPACE_PREFIXES:
        if column.startswith(prefix):
            return column[len(prefix) :]
    return column


def denamespace_csv(text: str) -> str:
    """Rewrite only the header line to the live (plain) column names.

    Data rows are left byte-for-byte intact (no re-quoting / re-formatting), so a
    mirror read is identical to what the parser saw from the live endpoint.
    """
    if not text:
        return text
    header, _, rest = text.partition("\n")
    new_header = ",".join(denamespace_column(c) for c in header.split(","))
    return f"{new_header}\n{rest}" if rest or text.endswith("\n") else new_header


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (numpy default) over pre-sorted values."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[lo] + frac * (sorted_values[lo + 1] - sorted_values[lo])


def extract_scores(scores_csv: str) -> list[float]:
    """Numeric ``score`` values from a (denamespaced) scores CSV, NA dropped."""
    _, rows = parse_scores_csv(scores_csv)
    return [r["score"] for r in rows if isinstance(r.get("score"), float)]


def extract_hgvs_rows(scores_csv: str, score_set_urn: str) -> list[dict[str, Any]]:
    """Normalised (variant_urn, hgvs_*) rows for the mirror hgvs_index.

    Keeps only rows naming a variant (``accession``) AND carrying at least one HGVS
    field; each HGVS is stored as its :func:`hgvs_core` (prefix-stripped, lowercased)
    so the resolver matches by equality on an indexed column.
    """
    _, rows = parse_scores_csv(scores_csv)
    out: list[dict[str, Any]] = []
    for row in rows:
        accession = row.get("accession")
        if not isinstance(accession, str):
            continue
        nt = row.get("hgvs_nt")
        pro = row.get("hgvs_pro")
        splice = row.get("hgvs_splice")
        if not any(isinstance(v, str) for v in (nt, pro, splice)):
            continue
        out.append(
            {
                "score_set_urn": score_set_urn,
                "variant_urn": accession,
                "hgvs_nt": hgvs_core(nt) if isinstance(nt, str) else None,
                "hgvs_pro": hgvs_core(pro) if isinstance(pro, str) else None,
                "hgvs_splice": hgvs_core(splice) if isinstance(splice, str) else None,
            }
        )
    return out


def compute_distribution(scores: list[float]) -> dict[str, Any]:
    """Summarise scores into n/min/max/mean + a 10-bin histogram + quantiles."""
    n = len(scores)
    if n == 0:
        return {"n": 0, "min": None, "max": None, "mean": None, "histogram": [], "quantiles": {}}
    lo, hi = min(scores), max(scores)
    mean = sum(scores) / n
    span = hi - lo
    counts = [0] * DISTRIBUTION_BINS
    for value in scores:
        idx = (
            DISTRIBUTION_BINS - 1
            if span == 0
            else min(int((value - lo) / span * DISTRIBUTION_BINS), DISTRIBUTION_BINS - 1)
        )
        counts[idx] += 1
    width = (span / DISTRIBUTION_BINS) if span else 0.0
    histogram = [
        {"bin_start": lo + i * width, "bin_end": lo + (i + 1) * width, "count": counts[i]}
        for i in range(DISTRIBUTION_BINS)
    ]
    ordered = sorted(scores)
    quantiles = {f"p{p}": _percentile(ordered, p) for p in _QUANTILE_POINTS}
    return {
        "n": n,
        "min": lo,
        "max": hi,
        "mean": mean,
        "histogram": histogram,
        "quantiles": quantiles,
    }


def parse_annotations(annotations_csv: str, score_set_urn: str) -> list[dict[str, Any]]:
    """Parse an annotations CSV into mapped-variant identity rows.

    Keeps only rows carrying a VRS id or a ClinGen allele id (the cross-dataset
    lookup keys); maps the dump's post-mapped HGVS columns to stable names.
    """
    text = denamespace_csv(annotations_csv)
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        vrs = _clean(raw.get("post_mapped_vrs_digest"))
        clingen = _clean(raw.get("clingen_allele_id"))
        if not vrs and not clingen:
            continue
        rows.append(
            {
                "variant_urn": _clean(raw.get("accession")),
                "score_set_urn": score_set_urn,
                "vrs_id": vrs,
                "clingen_allele_id": clingen,
                "post_mapped_hgvs_g": _clean(raw.get("post_mapped_hgvs_g")),
                "post_mapped_hgvs_p": _clean(raw.get("post_mapped_hgvs_p")),
                "post_mapped_hgvs_c": _clean(raw.get("post_mapped_hgvs_c")),
            }
        )
    return rows


def _clean(value: str | None) -> str | None:
    """Normalise a CSV cell: strip; empty/``NA`` -> None."""
    if value is None:
        return None
    text = value.strip()
    return None if text in ("", "NA") else text
