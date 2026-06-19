"""Tests for client-side search re-rank (DEF-2) and null-inclusive facets (DEF-3)."""

from __future__ import annotations

from mavedb_link.services.search import apply_sparse_facets, rank_by_target_match


def _ss(urn: str, name: str, organism: str | None = None, category: str = "protein_coding"):
    taxonomy = {"organismName": organism} if organism is not None else {}
    return {
        "urn": urn,
        "title": f"{name} set",
        "targetGenes": [
            {"name": name, "category": category, "targetSequence": {"taxonomy": taxonomy}}
        ],
    }


def test_rank_boosts_target_gene_above_namesake() -> None:
    # BAP1 ("BRCA1-Associated Protein 1") ranks first upstream; the BRCA1-target
    # set must be boosted above it for a gene-token query.
    items = [_ss("urn:mavedb:00000662-0-1", "BAP1"), _ss("urn:mavedb:00000081-a-1", "BRCA1")]
    ranked = rank_by_target_match(items, "BRCA1")
    assert ranked[0]["urn"] == "urn:mavedb:00000081-a-1"


def test_rank_is_stable_within_buckets() -> None:
    items = [
        _ss("urn:mavedb:1-a-1", "BAP1"),
        _ss("urn:mavedb:2-a-1", "BRCA1"),
        _ss("urn:mavedb:3-a-1", "BRCA1"),
        _ss("urn:mavedb:4-a-1", "TP53"),
    ]
    ranked = [r["urn"] for r in rank_by_target_match(items, "BRCA1")]
    # both BRCA1 first (original order preserved), then the non-matches in order
    assert ranked == [
        "urn:mavedb:2-a-1",
        "urn:mavedb:3-a-1",
        "urn:mavedb:1-a-1",
        "urn:mavedb:4-a-1",
    ]


def test_rank_noop_for_non_gene_query() -> None:
    items = [_ss("urn:mavedb:1-a-1", "BAP1"), _ss("urn:mavedb:2-a-1", "BRCA1")]
    # a concept/phrase query must not be re-ordered
    ranked = rank_by_target_match(items, "deep mutational scanning")
    assert [r["urn"] for r in ranked] == ["urn:mavedb:1-a-1", "urn:mavedb:2-a-1"]


def test_facets_are_null_inclusive_and_count_exclusions() -> None:
    items = [
        _ss("urn:mavedb:human-a-1", "BRCA2", organism="Homo sapiens"),
        _ss("urn:mavedb:unknown-a-1", "BRCA2", organism=None),  # empty organism upstream
        _ss("urn:mavedb:yeast-a-1", "BRCA2", organism="Saccharomyces cerevisiae"),
    ]
    kept, excluded = apply_sparse_facets(items, ["Homo sapiens"], None)
    urns = {k["urn"] for k in kept}
    assert "urn:mavedb:human-a-1" in urns
    assert "urn:mavedb:unknown-a-1" in urns  # null-inclusive: unknown != excluded
    assert "urn:mavedb:yeast-a-1" not in urns
    assert excluded == {"target_organism_names": 1}


def test_facets_noop_without_filters() -> None:
    items = [_ss("urn:mavedb:1-a-1", "BRCA2", organism="Homo sapiens")]
    kept, excluded = apply_sparse_facets(items, None, None)
    assert kept == items
    assert excluded == {}


def test_target_type_facet_excludes_known_mismatch() -> None:
    items = [
        _ss("urn:mavedb:1-a-1", "X", category="protein_coding"),
        _ss("urn:mavedb:2-a-1", "Y", category="regulatory"),
    ]
    kept, excluded = apply_sparse_facets(items, None, ["protein_coding"])
    assert {k["urn"] for k in kept} == {"urn:mavedb:1-a-1"}
    assert excluded == {"target_types": 1}
