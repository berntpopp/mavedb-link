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

from mavedb_link.exceptions import InvalidInputError
from mavedb_link.identifiers import looks_like_gene_symbol

#: The facets whose values are validated against the corpus vocabulary. Diverse by
#: nature (designed-protein names carry ``_`` and ``|``, organisms carry ``=``/``[]``,
#: authors carry accents), so a format/allowlist check would reject real values -- the
#: only reliable validator is the corpus set itself, which lives in the local mirror.
_CORPUS_FACETS = ("targets", "target_organism_names", "authors")


def _reject_blank(field: str, values: list[str] | None) -> None:
    """A blank/whitespace facet item is invalid_input, never a browse/empty match."""
    for value in values or []:
        if not value.strip():
            raise InvalidInputError(
                f"A blank/whitespace {field} value is not a filter. Remove the empty "
                "entry, or drop the facet to browse.",
                field=field,
            )


def _reject_unknown_exact(
    field: str, values: list[str] | None, vocab: set[str], *, upper: bool
) -> None:
    """Raise invalid_input for an exact-match facet value not in the corpus set.

    Does NOT skip on an empty ``vocab`` -- an empty corpus means the value is unknown,
    so a present value is still rejected (never silently matched to nothing).
    """
    for value in values or []:
        key = value.strip().upper() if upper else value.strip().lower()
        if key not in vocab:
            raise InvalidInputError(
                f"'{value}' is not a known MaveDB {field} value, so it would match "
                "nothing. Use search_score_sets(text=) to discover valid facet values, "
                "or drop the filter.",
                field=field,
            )


def validate_facet_values(
    client: Any,
    targets: list[str] | None,
    target_organism_names: list[str] | None,
    authors: list[str] | None,
    facet_mode: str | None = None,
) -> None:
    """Reject a facet argument that matches nothing (never a silent-empty result).

    ``facet_mode`` is a closed enum. Blank/whitespace items are rejected regardless of
    mirror presence. The corpus facets (``targets``/``target_organism_names`` exact,
    ``authors`` substring) are validated against the mirror's vocabulary. When no
    mirror is loaded (``client`` has no ``facet_vocabularies``), a facet value cannot
    be told from a valid one, so the call **fails closed** with ``invalid_input``
    rather than sending an unvalidated facet upstream and returning ``success:true,
    total:0`` -- the silent-empty the model cannot distinguish from "the data has none".
    Free-text search (``text=``) and ``get_gene_score_sets`` remain available live.
    """
    if facet_mode is not None and facet_mode not in ("inclusive", "strict"):
        raise InvalidInputError(
            f"Unknown facet_mode '{facet_mode}'.",
            field="facet_mode",
            allowed=["inclusive", "strict"],
        )
    facet_values = (targets, target_organism_names, authors)
    for field, values in zip(_CORPUS_FACETS, facet_values, strict=True):
        _reject_blank(field, values)
    if not any(facet_values):
        return
    vocab_fn = getattr(client, "facet_vocabularies", None)
    if not callable(vocab_fn):
        field = next(f for f, v in zip(_CORPUS_FACETS, facet_values, strict=True) if v)
        raise InvalidInputError(
            "Faceted search (targets/target_organism_names/authors) needs the local "
            "index, which is not loaded. Retry without the facet -- use text= free-text "
            "search, or get_gene_score_sets(gene_symbol=) for a gene.",
            field=field,
        )
    vocab = vocab_fn()
    _reject_unknown_exact("targets", targets, vocab.get("targets") or set(), upper=True)
    _reject_unknown_exact(
        "target_organism_names", target_organism_names, vocab.get("organisms") or set(), upper=False
    )
    author_vocab = vocab.get("authors") or set()
    for value in authors or []:
        needle = value.strip().lower()
        if not any(needle in name for name in author_vocab):
            raise InvalidInputError(
                f"No MaveDB author name contains '{value}', so it would match nothing. "
                "Try a surname, or drop the authors filter.",
                field="authors",
            )


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


def rank_experiments_by_target(
    items: list[dict[str, Any]], target_urns: set[str]
) -> list[dict[str, Any]]:
    """Stable-sort experiments so those that target the query gene rank first (A2).

    Experiment records carry no target-gene names (those live on their score sets),
    so ``target_urns`` is the set of experiment URNs found to target the gene via a
    score-set target search. A no-op when that set is empty.
    """
    if not target_urns:
        return list(items)
    return [
        item
        for _, item in sorted(
            enumerate(items),
            key=lambda pair: (0 if pair[1].get("urn") in target_urns else 1, pair[0]),
        )
    ]


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
