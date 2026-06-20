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

from mavedb_link.identifiers import variant_index_of

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


def _hgvs_core(value: str) -> str:
    """The HGVS body without an accession prefix (the part after the last ``:``).

    MaveDB stores hgvs_nt accession-prefixed in many sets
    (``ENST00000380152.8:c.8168A>G``), so comparing the prefix-stripped body lets a
    bare ``c.8168A>G`` resolve the prefixed stored value, and vice-versa (F5).
    """
    return value.rsplit(":", 1)[-1].strip()


def hgvs_matches(row: dict[str, Any], query_lower: str) -> bool:
    """Whether a parsed score row identifies the variant named by ``query_lower``.

    ``query_lower`` is the caller's hgvs (or variant URN), lower-cased. The
    ``accession`` (variant URN) is matched exactly; ``hgvs_nt`` / ``hgvs_pro`` match
    on the full string OR the accession-prefix-stripped body, so a bare
    ``c.8168A>G`` resolves a stored ``ENST...:c.8168A>G`` (F5). Protein queries can
    only match rows whose ``hgvs_pro`` is populated (many SGE sets leave it null).
    """
    query = query_lower.strip()
    accession = row.get("accession")
    if isinstance(accession, str) and accession.strip().lower() == query:
        return True
    query_core = _hgvs_core(query)
    for column in ("hgvs_nt", "hgvs_pro"):
        value = row.get(column)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized == query or _hgvs_core(normalized) == query_core:
            return True
    return False


#: The lean column set returned at response_mode="minimal" (F7b) -- enough to
#: align with get_mapped_variants and read/classify the score, no HGVS columns.
_MINIMAL_COLUMNS = ("accession", "variant_index", "score")


def shape_scores(
    text: str,
    *,
    start: int,
    limit: int,
    num_variants: int | None = None,
    response_mode: str = "compact",
) -> dict[str, Any]:
    """Parse the CSV page and wrap it in a pagination block.

    The MaveDB ``/scores`` endpoint applies ``start``/``limit`` server-side, so
    ``text`` is already a single page. ``truncated`` is conservative: a full page
    (``returned == limit``) signals more rows MAY remain; when the score set's
    ``num_variants`` is known it is used for an exact bound. ``response_mode``
    ``minimal`` drops the HGVS/extra columns to ``{accession, variant_index,
    score}`` so a large pull stays under the token cap (F7b).
    """
    columns, rows = parse_scores_csv(text)
    for row in rows:
        accession = row.get("accession")
        # Surface the numeric join key so callers align rows with get_mapped_variants
        # by value, never by fragile row position (F1).
        row["variant_index"] = variant_index_of(accession) if isinstance(accession, str) else None
    if response_mode == "minimal":
        rows = [{k: r[k] for k in _MINIMAL_COLUMNS if r.get(k) is not None} for r in rows]
        columns = list(_MINIMAL_COLUMNS)
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
