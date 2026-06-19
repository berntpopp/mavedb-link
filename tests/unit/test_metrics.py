"""Tests for the in-process runtime metrics collector."""

from __future__ import annotations

from mavedb_link.mcp import metrics


def test_records_and_snapshots() -> None:
    metrics.reset()
    metrics.record("get_score_set", 10, ok=True)
    metrics.record("get_score_set", 30, ok=True)
    metrics.record("get_score_set", 50, ok=False)
    snap = metrics.snapshot()
    assert snap["requests"] == 3
    assert snap["errors"] == 1
    assert snap["latency_ms"]["p50"] >= 10
    assert snap["per_tool"]["get_score_set"] == {"requests": 3, "errors": 1}


def test_error_rate_withheld_below_threshold() -> None:
    metrics.reset()
    metrics.record("x", 5, ok=False)
    assert metrics.snapshot()["error_rate"] is None


def test_error_rate_reported_above_threshold() -> None:
    metrics.reset()
    for _ in range(25):
        metrics.record("x", 5, ok=True)
    assert metrics.snapshot()["error_rate"] == 0.0


def test_reset_clears() -> None:
    metrics.record("x", 5, ok=True)
    metrics.reset()
    assert metrics.snapshot()["requests"] == 0
