"""Parse and page the MaveDB variant-scores CSV.

``GET /score-sets/{urn}/scores`` returns ``text/csv``: a header row then one row
per scored variant, with ``accession``, the HGVS columns (``hgvs_nt``,
``hgvs_splice``, ``hgvs_pro``), and the quantitative ``score`` (plus score-set
specific columns such as ``sd``/``se``). Absent values are ``NA``.

This module parses the CSV into typed rows (numeric columns coerced to float,
``NA`` -> ``None``) and reports an honest pagination block.
"""

from __future__ import annotations

import csv
import io
from typing import Any

#: Columns that always stay strings (identifiers / HGVS), never coerced to float.
_STRING_COLUMNS = frozenset({"accession", "hgvs_nt", "hgvs_splice", "hgvs_pro", "guide_sequence"})


def _coerce(column: str, value: str) -> Any:
    """Coerce a CSV cell: ``NA``/empty -> None; numeric -> float; else string.

    Identifier/HGVS columns are never numeric-coerced and only the unambiguous
    empty/``NA`` tokens null them, so a literal string like ``None`` survives.
    """
    text = value.strip()
    if column in _STRING_COLUMNS:
        return None if text in ("", "NA") else text
    if text in ("", "NA", "NaN", "None", "null"):
        return None
    try:
        return float(text)
    except ValueError:
        return text


def parse_scores_csv(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Parse a scores CSV into ``(columns, rows)`` with coerced cell values."""
    reader = csv.reader(io.StringIO(text))
    rows_iter = iter(reader)
    try:
        header = next(rows_iter)
    except StopIteration:
        return [], []
    columns = [c.strip() for c in header]
    parsed: list[dict[str, Any]] = []
    for raw in rows_iter:
        if not raw or all(cell.strip() == "" for cell in raw):
            continue
        record = {
            columns[i]: _coerce(columns[i], raw[i]) for i in range(min(len(columns), len(raw)))
        }
        parsed.append(record)
    return columns, parsed


#: Columns a single-variant hgvs lookup matches against (case-insensitive, exact).
_HGVS_MATCH_COLUMNS = ("hgvs_nt", "hgvs_pro", "accession")


def hgvs_matches(row: dict[str, Any], query_lower: str) -> bool:
    """Whether a parsed score row identifies the variant named by ``query_lower``.

    ``query_lower`` is the caller's hgvs (or variant URN) lower-cased; a row matches
    on an exact, case-insensitive equality with its hgvs_nt/hgvs_pro/accession cell.
    """
    for column in _HGVS_MATCH_COLUMNS:
        value = row.get(column)
        if isinstance(value, str) and value.strip().lower() == query_lower:
            return True
    return False


def shape_scores(
    text: str, *, start: int, limit: int, num_variants: int | None = None
) -> dict[str, Any]:
    """Parse the CSV page and wrap it in a pagination block.

    The MaveDB ``/scores`` endpoint applies ``start``/``limit`` server-side, so
    ``text`` is already a single page. ``truncated`` is conservative: a full page
    (``returned == limit``) signals more rows MAY remain; when the score set's
    ``num_variants`` is known it is used for an exact bound.
    """
    columns, rows = parse_scores_csv(text)
    returned = len(rows)
    truncated = start + returned < num_variants if num_variants is not None else returned >= limit
    return {
        "columns": columns,
        "rows": rows,
        "returned": returned,
        "start": start,
        "limit": limit,
        "total": num_variants,
        "truncated": truncated,
        "next_start": start + returned if truncated else None,
    }
