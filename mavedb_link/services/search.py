"""Client-side relevance re-rank and null-inclusive faceting for score-set search.

The MaveDB ``/score-sets/search`` endpoint returns the FULL match list, and
faceting on sparse upstream metadata (``organism``, target type) silently
over-filters. This module operates on RAW upstream score-set dicts to:

- **DEF-2** re-rank gene-token queries so target-gene matches outrank name/abstract
  substring matches (a stable sort: matches first, original order within buckets).
- **DEF-3** apply organism/target-type facets client-side, *null-inclusively* (a
  record with unknown metadata is kept, not silently dropped), and report an honest
  ``facet_excluded`` count of records that had a known, non-matching value.

Pure functions so they unit-test in isolation.
"""

from __future__ import annotations

from typing import Any

from mavedb_link.identifiers import looks_like_gene_symbol


def _target_names(raw: dict[str, Any]) -> set[str]:
    """Lower-cased target-gene names of a raw score-set record."""
    return {
        str(t.get("name")).strip().lower()
        for t in raw.get("targetGenes") or []
        if isinstance(t, dict) and t.get("name")
    }


def _target_organisms(raw: dict[str, Any]) -> set[str]:
    """Lower-cased, non-empty target organism names of a raw score-set record."""
    organisms: set[str] = set()
    for target in raw.get("targetGenes") or []:
        if not isinstance(target, dict):
            continue
        taxonomy = (target.get("targetSequence") or {}).get("taxonomy") or {}
        name = taxonomy.get("organismName")
        if isinstance(name, str) and name.strip():
            organisms.add(name.strip().lower())
    return organisms


def _target_categories(raw: dict[str, Any]) -> set[str]:
    """Lower-cased, non-empty target categories (target types) of a raw record."""
    return {
        str(t.get("category")).strip().lower()
        for t in raw.get("targetGenes") or []
        if isinstance(t, dict) and t.get("category")
    }


def rank_by_target_match(items: list[dict[str, Any]], text: str | None) -> list[dict[str, Any]]:
    """Stable-sort score sets so target-gene matches for a gene-token query rank first.

    A no-op unless ``text`` looks like a gene symbol (so concept/phrase searches keep
    the upstream relevance order).
    """
    if not text or not looks_like_gene_symbol(text.strip()):
        return list(items)
    query = text.strip().lower()
    return [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda pair: (0 if query in _target_names(pair[1]) else 1, pair[0]),
        )
    ]


def _facet_drops(values: set[str], facet: set[str] | None, *, strict: bool) -> bool:
    """Whether a record is dropped for one facet.

    ``None`` facet never drops. A match is always kept. On a non-match the record
    is dropped if it had a KNOWN value (inclusive default) or always (``strict``,
    which therefore also drops empty/unknown metadata).
    """
    if facet is None or (values & facet):
        return False
    return bool(values) or strict


def apply_sparse_facets(
    items: list[dict[str, Any]],
    organisms: list[str] | None,
    target_types: list[str] | None,
    *,
    strict: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter by organism/target-type client-side, counting drops.

    Default (``strict=False``) is null-inclusive: a record is excluded only when it
    has a KNOWN value that does not match; empty/unknown metadata is kept. With
    ``strict=True`` a record whose facet metadata is empty/unknown is ALSO dropped
    (F9). Returns ``(kept, excluded_counts)`` where ``excluded_counts`` omits zeros.
    """
    if not organisms and not target_types:
        return list(items), {}
    org_filter = {o.strip().lower() for o in organisms} if organisms else None
    type_filter = {t.strip().lower() for t in target_types} if target_types else None
    kept: list[dict[str, Any]] = []
    excluded = {"target_organism_names": 0, "target_types": 0}
    for item in items:
        if _facet_drops(_target_organisms(item), org_filter, strict=strict):
            excluded["target_organism_names"] += 1
            continue
        if _facet_drops(_target_categories(item), type_filter, strict=strict):
            excluded["target_types"] += 1
            continue
        kept.append(item)
    return kept, {k: v for k, v in excluded.items() if v}
