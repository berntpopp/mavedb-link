"""Tests for argument ergonomics (aliases, did-you-mean, constraints, signatures)."""

from __future__ import annotations

from mavedb_link.mcp import arg_help


def test_normalize_alias_applies_when_canonical_valid() -> None:
    new_args, applied = arg_help.normalize_alias_args(["text", "limit"], {"query": "BRCA1"})
    assert new_args == {"text": "BRCA1"}
    assert applied == [("query", "text")]


def test_normalize_alias_skipped_when_canonical_absent() -> None:
    # 'symbol' is not a param of this tool, so 'gene' is left untouched.
    new_args, applied = arg_help.normalize_alias_args(["text"], {"gene": "BRCA1"})
    assert new_args == {"gene": "BRCA1"}
    assert applied == []


def test_normalize_alias_explicit_canonical_wins() -> None:
    new_args, applied = arg_help.normalize_alias_args(["text"], {"query": "a", "text": "b"})
    assert new_args == {"text": "b"}
    assert applied == []


def test_did_you_mean_via_alias_and_fuzzy() -> None:
    assert arg_help.did_you_mean("query", ["text", "limit"]) == "text"
    assert arg_help.did_you_mean("lim", ["limit", "offset"]) == "limit"
    assert arg_help.did_you_mean("zzz", ["text"]) is None


def test_describe_constraints_enum() -> None:
    schema = {"enum": ["minimal", "compact", "standard", "full"]}
    allowed, human = arg_help.describe_constraints(schema)
    assert allowed == ["minimal", "compact", "standard", "full"]
    assert "one of" in human


def test_describe_constraints_range() -> None:
    schema = {"type": "integer", "minimum": 1, "maximum": 100}
    allowed, human = arg_help.describe_constraints(schema)
    assert allowed == ["1..100"]
    assert "between" in human


def test_describe_constraints_none_for_plain_string() -> None:
    assert arg_help.describe_constraints({"type": "string"}) is None


def test_describe_type_expectation() -> None:
    allowed, human = arg_help.describe_type_expectation(
        {"type": "array", "items": {"type": "string"}, "examples": [["a"]]}
    )
    assert "array of strings" in human
    assert allowed == ['["a"]']


def test_tool_signature_renders_required_then_optional() -> None:
    schema = {
        "properties": {"urn": {}, "response_mode": {}},
        "required": ["urn"],
    }
    assert arg_help.tool_signature("get_score_set", schema) == "get_score_set(urn, response_mode=)"
