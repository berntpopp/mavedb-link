"""Tests for MaveDB identifier parsing/classification/validation."""

from __future__ import annotations

import pytest

from mavedb_link import identifiers as ids
from mavedb_link.exceptions import InvalidInputError


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("urn:mavedb:00000001", "experiment_set"),
        ("urn:mavedb:00000001-a", "experiment"),
        ("urn:mavedb:00000001-a-1", "score_set"),
        ("urn:mavedb:00000001-0-1", "score_set"),
        ("urn:mavedb:00000001-a-1#2044", "variant"),
        ("tmp:0d1f2e3a-4b5c-6d7e-8f90-1a2b3c4d5e6f", "tmp"),
        ("BRCA1", None),
        ("not-a-urn", None),
        ("", None),
    ],
)
def test_classify_urn(value: str, kind: str | None) -> None:
    assert ids.classify_urn(value) == kind


def test_specific_predicates() -> None:
    assert ids.is_score_set_urn("urn:mavedb:00000001-a-1")
    assert not ids.is_score_set_urn("urn:mavedb:00000001-a")
    assert ids.is_experiment_urn("urn:mavedb:00000001-a")
    assert ids.is_experiment_set_urn("urn:mavedb:00000001")
    assert ids.is_variant_urn("urn:mavedb:00000001-a-1#7")
    assert ids.looks_like_mavedb_urn("urn:mavedb:00000001-a-1")
    assert not ids.looks_like_mavedb_urn("UBE2I")


def test_whitespace_is_trimmed() -> None:
    assert ids.is_score_set_urn("  urn:mavedb:00000001-a-1  ")
    assert ids.classify_urn("  urn:mavedb:00000001  ") == "experiment_set"


def test_gene_symbol_detection() -> None:
    assert ids.looks_like_gene_symbol("BRCA1")
    assert ids.looks_like_gene_symbol("TP53")
    assert ids.looks_like_gene_symbol("C1orf127".upper())
    assert not ids.looks_like_gene_symbol("urn:mavedb:00000001-a-1")
    assert not ids.looks_like_gene_symbol("lowercase")


def test_score_set_urn_of_variant() -> None:
    assert ids.score_set_urn_of_variant("urn:mavedb:00000001-a-1#42") == "urn:mavedb:00000001-a-1"
    assert ids.score_set_urn_of_variant("urn:mavedb:00000001-a-1") is None


def test_variant_index_of() -> None:
    # The trailing #<index> parsed as an int so callers can sort/join numerically
    # (lexical sort of the URN string would order #1, #10, #100, ... #2).
    assert ids.variant_index_of("urn:mavedb:00000001-a-1#2044") == 2044
    assert ids.variant_index_of("urn:mavedb:00000001-a-1#1") == 1
    assert ids.variant_index_of("  urn:mavedb:00000001-a-1#7  ") == 7
    assert ids.variant_index_of("urn:mavedb:00000001-a-1") is None
    assert ids.variant_index_of("not-a-urn") is None
    assert ids.variant_index_of("") is None


def test_validate_score_set_urn_ok() -> None:
    assert ids.validate_score_set_urn("urn:mavedb:00000001-a-1") == "urn:mavedb:00000001-a-1"


def test_validate_score_set_urn_rejects() -> None:
    with pytest.raises(InvalidInputError) as exc:
        ids.validate_score_set_urn("urn:mavedb:00000001-a")
    assert exc.value.field == "urn"
