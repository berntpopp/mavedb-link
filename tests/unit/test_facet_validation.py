"""Contract-hardening v1: closed error_code enum + facet silent-empty rejection.

- ``_canonicalize_code`` folds every off-enum code onto the closed six-value enum
  and retains the specific cause additively in ``error_subtype``.
- ``validate_facet_values`` rejects a facet value the corpus can never match with a
  naming ``invalid_input`` -- never a zero-row ``success:true`` (the silent-empty bug).
"""

from __future__ import annotations

import pytest

from mavedb_link.exceptions import InvalidInputError
from mavedb_link.mcp.envelope import CANONICAL_ERROR_CODES, _canonicalize_code
from mavedb_link.services.search import validate_facet_values


@pytest.mark.parametrize(
    ("code", "expected", "subtype"),
    [
        ("data_unavailable", "upstream_unavailable", "data_unavailable"),
        ("response_too_large", "invalid_input", "response_too_large"),
        ("internal_error", "internal", "internal_error"),
        ("validation_failed", "invalid_input", "validation_failed"),
        ("not_found", "not_found", None),  # already canonical: unchanged, no subtype
        ("weird_unknown", "internal", "weird_unknown"),  # unknown -> internal, retain detail
    ],
)
def test_canonicalize_code_maps_onto_closed_enum(
    code: str, expected: str, subtype: str | None
) -> None:
    mapped, sub = _canonicalize_code(code)
    assert mapped in CANONICAL_ERROR_CODES
    assert mapped == expected
    assert sub == subtype


class _VocabClient:
    """Duck-typed stand-in for a mirror-backed client exposing facet vocab."""

    def facet_vocabularies(self) -> dict[str, set[str]]:
        return {
            "targets": {"BRCA1", "TP53"},  # upper-cased, as gene_index matches
            "organisms": {"homo sapiens"},
            "authors": {"lea m starita", "jesse d bloom"},
        }


def test_facet_mode_out_of_enum_is_rejected() -> None:
    with pytest.raises(InvalidInputError) as exc:
        validate_facet_values(None, None, None, None, facet_mode="loose")
    assert exc.value.field == "facet_mode"


def test_unknown_target_is_invalid_input_not_silent_empty() -> None:
    with pytest.raises(InvalidInputError) as exc:
        validate_facet_values(_VocabClient(), ["__bogus__"], None, None)
    assert exc.value.field == "targets"


def test_known_target_case_insensitive_passes() -> None:
    # brca1 (lower) resolves against the upper-cased corpus set -> no raise.
    validate_facet_values(_VocabClient(), ["brca1"], None, None)


def test_unknown_organism_is_invalid_input() -> None:
    with pytest.raises(InvalidInputError) as exc:
        validate_facet_values(_VocabClient(), None, ["Martian"], None)
    assert exc.value.field == "target_organism_names"


def test_author_substring_matches_but_nonsense_is_rejected() -> None:
    validate_facet_values(_VocabClient(), None, None, ["Starita"])  # substring hit
    with pytest.raises(InvalidInputError) as exc:
        validate_facet_values(_VocabClient(), None, None, ["Nonexistent"])
    assert exc.value.field == "authors"


def test_no_vocab_falls_through_best_effort() -> None:
    class _LiveOnly:
        pass  # no facet_vocabularies -> live-only, validation is a no-op

    # Must NOT raise: with no mirror there is no corpus to validate against.
    validate_facet_values(_LiveOnly(), ["__bogus__"], ["Martian"], ["Nonexistent"])
