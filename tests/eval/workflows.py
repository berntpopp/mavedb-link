"""Realistic multi-call eval workflows with verifiable outcomes (Phase 5.1).

The three seed cases mirror the consumer-review session (gene discovery, single-
variant lookup, calibrated interpretation); ``HELD_OUT`` carries tasks kept out of
the tuned set so descriptions are not overfit to the headline workflows (5.3).
"""

from __future__ import annotations

from tests import fixtures
from tests.eval.harness import Recorder, Workflow


async def gene_discovery(rec: Recorder) -> None:
    """Gene -> dataset -> scores (the canonical find->record->scores path)."""
    hits = await rec.call("search_score_sets", {"text": "UBE2I"})
    rec.check(hits["results"][0]["urn"] == fixtures.SCORE_SET_URN, "top hit urn")
    top = hits["results"][0]["urn"]
    record = await rec.call("get_score_set", {"urn": top})
    rec.check(record["urn"] == top, "score-set urn")
    scores = await rec.call("get_variant_scores", {"urn": top, "limit": 3})
    rec.check(scores["returned"] == 3, "three rows returned")
    rec.check(scores["rows"][0]["score"] == 0.5, "first row score")


async def single_variant_lookup(rec: Recorder) -> None:
    """Score-set + hgvs -> calibrated class, then roll up across every score set."""
    one = await rec.call("get_variant_score", {"urn": fixtures.SCORE_SET_URN, "hgvs": "c.2T>G"})
    rec.check(one["variants"][0]["score"] == -1.2, "variant score")
    rec.check(
        one["variants"][0]["classifications"][0]["classification"] == "abnormal",
        "calibrated class",
    )
    variant_urn = one["variants"][0]["variant_urn"]
    rollup = await rec.call("find_variant", {"variant_urn": variant_urn})
    rec.check(rollup["resolved_by"] == "variant_urn", "resolved by variant_urn")
    rec.check(rollup["total"] >= 2, "spans >=2 score sets")


async def calibrated_interpretation(rec: Recorder) -> None:
    """Distribution percentile + class, then the full abnormal (PS3) variant list."""
    record = await rec.call("get_score_set", {"urn": fixtures.SCORE_SET_URN})
    rec.check(bool(record.get("score_calibrations")), "calibrations surfaced")
    # Two numeric scores in the fixture CSV (-1.2, 0.5); -0.3 sits at the 50th pct
    # and falls in the abnormal bin of the primary calibration.
    dist = await rec.call("get_score_distribution", {"urn": fixtures.SCORE_SET_URN, "score": -0.3})
    rec.check(dist["query"]["percentile"] == 50.0, "query percentile")
    rec.check(dist["query"]["classifications"][0]["classification"] == "abnormal", "query class")
    abnormal = await rec.call(
        "get_classified_variants",
        {"urn": fixtures.SCORE_SET_URN, "classification": "abnormal"},
    )
    rec.check(abnormal["variants"][0]["classification"] == "abnormal", "abnormal listed")


async def follow_the_chain(rec: Recorder) -> None:
    """Trust the server's steering: follow _meta.next_commands[0] hop to hop (A5/G5)."""
    payload = await rec.call("get_variant_score", {"urn": fixtures.VARIANT_URN})
    step = payload["_meta"]["next_commands"][0]
    rec.check(step["tool"] == "find_variant", "chains to cross-dataset rollup")
    nxt = await rec.call(step["tool"], step["arguments"])
    rec.check(nxt["success"] is True, "chained call succeeds")


async def variant_rollup_by_urn(rec: Recorder) -> None:
    """Held-out: one-hop cross-dataset rollup from a variant URN (no map-first)."""
    out = await rec.call("find_variant", {"variant_urn": fixtures.VARIANT_URN})
    rec.check(out["vrs_id"] == fixtures.VRS_ID, "vrs resolved from variant urn")
    rec.check({h["score_set_urn"] for h in out["hits"]} >= {fixtures.SCORE_SET_URN}, "hits")


#: Tuned workflows (the seed cases) + the chaining UX check.
WORKFLOWS: dict[str, Workflow] = {
    "gene_discovery": gene_discovery,
    "single_variant_lookup": single_variant_lookup,
    "calibrated_interpretation": calibrated_interpretation,
    "follow_the_chain": follow_the_chain,
}

#: Held-out tasks (not used to tune tool descriptions) to catch overfitting.
HELD_OUT: dict[str, Workflow] = {
    "variant_rollup_by_urn": variant_rollup_by_urn,
}

ALL_WORKFLOWS: dict[str, Workflow] = {**WORKFLOWS, **HELD_OUT}
