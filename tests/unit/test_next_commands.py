"""Tests for the next_commands chainers and error-recovery steps."""

from __future__ import annotations

from mavedb_link.mcp import next_commands as nc


def test_cmd_shape() -> None:
    assert nc.cmd("get_score_set", urn="x") == {
        "tool": "get_score_set",
        "arguments": {"urn": "x"},
    }


def test_after_capabilities_starts_workflow() -> None:
    steps = nc.after_capabilities()
    assert steps[0]["tool"] == "search_score_sets"


def test_after_search_opens_top_hit_and_pages() -> None:
    payload = {
        "results": [{"urn": "urn:mavedb:00000001-a-1"}],
        "truncated": True,
        "next_offset": 25,
        "total": 60,
    }
    steps = nc.after_search_score_sets("BRCA1", payload)
    assert steps[0] == nc.cmd("get_score_set", urn="urn:mavedb:00000001-a-1")
    # a forward-page step (offset advanced) is present
    assert any(
        s["tool"] == "search_score_sets" and s["arguments"].get("offset") == 25 for s in steps
    )


def test_after_search_empty_falls_back() -> None:
    steps = nc.after_search_score_sets("BRCA1", {"results": []})
    assert steps[0]["tool"] == "get_gene_score_sets"


def test_after_get_score_set_chains_to_scores() -> None:
    steps = nc.after_get_score_set(
        {"urn": "urn:mavedb:00000001-a-1", "experiment_urn": "urn:mavedb:00000001-a"}
    )
    tools = [s["tool"] for s in steps]
    assert "get_variant_scores" in tools
    assert "get_mapped_variants" in tools
    assert "get_experiment" in tools


def test_after_variant_scores_pages_by_start() -> None:
    payload = {"urn": "urn:mavedb:00000001-a-1", "truncated": True, "next_start": 100}
    steps = nc.after_get_variant_scores(payload)
    assert any(s["arguments"].get("start") == 100 for s in steps)


def test_after_variant_score_rolls_up_then_opens_score_set() -> None:
    # 2.2 consolidation: the cross-dataset rollup (find_variant by variant_urn) is
    # offered first, then the parent score set + its genome mapping.
    steps = nc.after_get_variant_score(
        {
            "urn": "urn:mavedb:00000001-a-1",
            "resolved_by": "variant_urn",
            "variants": [{"variant_urn": "urn:mavedb:00000001-a-1#2"}],
        }
    )
    assert steps[0] == nc.cmd("find_variant", variant_urn="urn:mavedb:00000001-a-1#2")
    assert any(s == nc.cmd("get_score_set", urn="urn:mavedb:00000001-a-1") for s in steps)
    assert any(s["tool"] == "get_mapped_variants" for s in steps)


def test_after_variant_score_no_variants_uses_score_set() -> None:
    steps = nc.after_get_variant_score({"urn": "urn:mavedb:00000001-a-1"})
    assert steps[0] == nc.cmd("get_score_set", urn="urn:mavedb:00000001-a-1")


def test_after_gene_opens_first_dataset() -> None:
    payload = {"score_sets": [{"urn": "urn:mavedb:00000001-a-1"}], "gene": {"symbol": "UBE2I"}}
    steps = nc.after_get_gene_score_sets(payload)
    assert steps[0] == nc.cmd("get_score_set", urn="urn:mavedb:00000001-a-1")


def test_after_collection_pages_forward_when_truncated() -> None:
    # F12: a truncated collection offers a forward-page (offset) next step.
    payload = {
        "urn": "abcdEFGH",
        "score_set_urns": ["urn:mavedb:00000001-a-1"],
        "truncated": True,
        "next_offset": 100,
        "total": 250,
    }
    steps = nc.after_get_collection(payload)
    assert steps[0] == nc.cmd("get_score_set", urn="urn:mavedb:00000001-a-1")
    assert any(s["arguments"].get("offset") == 100 for s in steps)


def test_default_error_routes_upstream_to_diagnostics() -> None:
    steps = nc.default_error_next_commands("get_score_set", "upstream_unavailable", {})
    assert steps == [nc.cmd("get_diagnostics")]


def test_default_error_non_urn_routes_to_search() -> None:
    steps = nc.default_error_next_commands("get_score_set", "not_found", {"urn": "BRCA1"})
    assert steps[0]["tool"] == "search_score_sets"
    assert steps[0]["arguments"]["text"] == "BRCA1"


def test_after_gene_widen_respects_gene_limit_ceiling() -> None:
    # Regression: a widen step must not suggest a limit the tool would reject
    # (get_gene_score_sets is bounded le=MAX_GENE_LIMIT=100, not MAX_SCORES_LIMIT).
    payload = {
        "score_sets": [{"urn": "urn:mavedb:00000001-a-1"}],
        "gene": {"symbol": "BRCA1"},
        "truncated": True,
        "next_offset": 20,
        "total": 150,
    }
    steps = nc.after_get_gene_score_sets(payload)
    widen = [s for s in steps if "limit" in s["arguments"]]
    assert widen, "expected a widen step for a truncated gene result"
    for step in widen:
        assert step["arguments"]["limit"] <= 100


def test_after_find_variant_opens_first_hit_set_and_variant() -> None:
    payload = {
        "vrs_id": "ga4gh:VA.x",
        "hits": [
            {"score_set_urn": "urn:mavedb:00000001-a-1", "variant_urn": "urn:mavedb:00000001-a-1#2"}
        ],
    }
    steps = nc.after_find_variant(payload)
    tools = [s["tool"] for s in steps]
    assert tools[:2] == ["get_score_set", "get_variant_score"]


def test_after_hgvs_validation_valid_searches() -> None:
    assert nc.after_get_hgvs_validation({"valid": True})[0]["tool"] == "search_score_sets"
    assert nc.after_get_hgvs_validation({"valid": False})[0]["tool"] == "get_server_capabilities"


def test_after_classified_variants_opens_variant_and_set() -> None:
    payload = {
        "urn": "urn:mavedb:00000001-a-1",
        "classification": "abnormal",
        "variants": [{"variant_urn": "urn:mavedb:00000001-a-1#2"}],
    }
    steps = nc.after_get_classified_variants(payload)
    tools = [s["tool"] for s in steps]
    assert "get_variant_score" in tools
    assert "get_score_set" in tools
