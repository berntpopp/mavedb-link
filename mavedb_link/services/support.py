"""Pure helpers shared by the MaveDB service methods (data-plane support).

Extraction of the response-shaping plumbing that every list/search method reuses:
clamping page sizes, pulling ``(items, total)`` from a search wrapper, the uniform
pagination block, and the numeric mapped-variant ordering. Kept free of any client
or I/O so they unit-test in isolation and keep ``mavedb_service`` focused.
"""

from __future__ import annotations

from typing import Any

from mavedb_link.identifiers import variant_index_of


def clamp(value: int, lo: int, hi: int) -> int:
    """Clamp ``value`` into ``[lo, hi]``."""
    return max(lo, min(value, hi))


def extract_items(
    resp: Any, item_keys: tuple[str, ...], total_keys: tuple[str, ...]
) -> tuple[list[Any], int | None]:
    """Pull ``(items, total)`` from a search response (list or wrapper dict)."""
    if isinstance(resp, list):
        return resp, len(resp)
    if isinstance(resp, dict):
        for key in item_keys:
            if isinstance(resp.get(key), list):
                items = resp[key]
                total = next((resp[t] for t in total_keys if isinstance(resp.get(t), int)), None)
                return items, total
    return [], 0


def mapped_variant_urn(item: Any) -> str:
    """A mapped-variant record's source variant URN (or empty)."""
    if not isinstance(item, dict):
        return ""
    return item.get("variantUrn") or (item.get("variant") or {}).get("urn") or ""


def mapped_sort_key(item: Any) -> tuple[int, str]:
    """Numeric sort key (``#index``, urn) so rows order #1,#2,…,#10 — not #1,#10,#2.

    Lexical sort of the variant URN string mispairs rows when zipped against the
    numerically-ordered scores table (F1). Variants with no parseable index sort
    last (deterministically, by URN string).
    """
    urn = mapped_variant_urn(item)
    index = variant_index_of(urn)
    return (index if index is not None else 2**62, urn)


def page_block(*, total: int | None, returned: int, limit: int, offset: int) -> dict[str, Any]:
    """Build the uniform pagination block for a list payload."""
    truncated = offset + returned < total if total is not None else returned >= limit
    return {
        "total": total,
        "returned": returned,
        "limit": limit,
        "offset": offset,
        "truncated": truncated,
        "next_offset": offset + returned if truncated else None,
    }
