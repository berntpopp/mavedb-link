"""Contract-hardening v1: closed error_code enum + facet silent-empty rejection.

- ``_canonicalize_code`` folds every off-enum code onto the closed six-value enum
  and retains the specific cause additively in ``error_subtype``.
- ``validate_facet_values`` rejects a facet value the corpus can never match with a
  naming ``invalid_input`` -- never a zero-row ``success:true`` (the silent-empty bug).
"""

from __future__ import annotations

import pytest
from fastmcp.tools.tool import ToolResult

from mavedb_link.exceptions import InvalidInputError
from mavedb_link.mcp.envelope import (
    CANONICAL_ERROR_CODES,
    McpErrorContext,
    McpToolError,
    _canonicalize_code,
    run_mcp_tool,
)
from mavedb_link.services.search import validate_facet_values


async def test_raised_off_enum_mcptoolerror_is_canonicalized_at_the_wire() -> None:
    # The RAISED path (not just constructors): an off-enum McpToolError raised inside a
    # tool body must emerge on the wire as isError:true with a closed-enum error_code
    # and the specific cause retained in error_subtype.
    async def _boom() -> dict[str, object]:
        raise McpToolError(error_code="validation_failed", message="bad input")

    result = await run_mcp_tool("get_score_set", _boom, context=McpErrorContext("get_score_set"))
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    env = result.structured_content
    assert isinstance(env, dict)
    assert env["error_code"] == "invalid_input"
    assert env["error_code"] in CANONICAL_ERROR_CODES
    assert env["error_subtype"] == "validation_failed"


async def test_returned_off_enum_success_false_is_canonicalized_at_the_wire() -> None:
    # The RETURNED path: a body that returns success:false with an off-enum code is
    # also folded onto the closed enum at egress (never leaks off-enum to the wire).
    async def _body() -> dict[str, object]:
        return {"success": False, "error_code": "data_unavailable", "message": "x"}

    result = await run_mcp_tool("get_score_set", _body, context=McpErrorContext("get_score_set"))
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    env = result.structured_content
    assert isinstance(env, dict)
    assert env["error_code"] == "upstream_unavailable"
    assert env["error_subtype"] == "data_unavailable"


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


class _LiveOnly:
    """A mirror-less client (no facet_vocabularies) -- the live-only fallback."""


@pytest.mark.parametrize(
    "facets",
    [(["BRCA1"], None, None), (None, ["Homo sapiens"], None), (None, None, ["Starita"])],
)
def test_no_mirror_fails_closed_not_silent_empty(
    facets: tuple[list[str] | None, list[str] | None, list[str] | None],
) -> None:
    # Without a mirror a facet value cannot be validated, so the call must fail closed
    # with invalid_input -- NEVER pass the facet upstream and return success:true,0.
    # (This replaces the earlier test that ratified the silent-empty regression.)
    with pytest.raises(InvalidInputError):
        validate_facet_values(_LiveOnly(), *facets)


def test_no_mirror_text_only_search_is_still_allowed() -> None:
    # No facets applied -> nothing to validate -> live text search still works.
    validate_facet_values(_LiveOnly(), None, None, None)


@pytest.mark.parametrize(
    ("facets", "field"),
    [
        ((["  "], None, None), "targets"),
        ((None, [" "], None), "target_organism_names"),
        ((None, None, ["\t"]), "authors"),
        ((["BRCA1", ""], None, None), "targets"),
    ],
)
def test_blank_or_whitespace_facet_item_is_invalid_input(
    facets: tuple[list[str] | None, list[str] | None, list[str] | None], field: str
) -> None:
    # A blank/whitespace facet item is invalid_input regardless of mirror presence --
    # it must never degrade to a browse or a silent-empty (with a mirror it would
    # otherwise produce no URNs / an empty matcher).
    with pytest.raises(InvalidInputError) as exc:
        validate_facet_values(_VocabClient(), *facets)
    assert exc.value.field == field
