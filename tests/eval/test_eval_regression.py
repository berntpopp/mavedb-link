"""CI regression gate over the eval workflows (Phase 5.2/5.3/5.4).

Runs every realistic workflow through the full mocked facade and fails on:
- any accuracy regression (a verifiable outcome broke),
- any error-rate regression vs the committed baseline,
- a tool-call-count regression, or
- a >10% token-per-task regression.

Improvements (fewer tokens/calls) never fail the gate; regenerate the baseline
with `make eval-baseline` after an intentional surface change. This file is a
normal (non-integration) test, so it runs inside `make ci-local`.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import respx

from tests.eval.harness import Recorder, mock_surface
from tests.eval.workflows import ALL_WORKFLOWS

#: Allowed token growth before the gate fails (per Anthropic A8: token-per-task).
_TOKEN_REGRESSION_TOLERANCE = 1.10

_BASELINE: dict[str, dict[str, int]] = json.loads(
    (pathlib.Path(__file__).parent / "baseline.json").read_text()
)


@pytest.mark.parametrize("name", sorted(ALL_WORKFLOWS))
async def test_workflow_within_budget(
    name: str, respx_router: respx.Router, facade: Any, structured: Any
) -> None:
    mock_surface(respx_router)
    rec = Recorder(facade, structured)
    await ALL_WORKFLOWS[name](rec)
    m = rec.metrics()
    base = _BASELINE[name]

    assert m["accuracy"] == 1.0, f"{name}: accuracy regressed (a verifiable outcome broke)"
    assert m["errors"] <= base["errors"], f"{name}: error-rate regression"
    assert m["tool_calls"] <= base["tool_calls"], (
        f"{name}: tool-call regression {m['tool_calls']} > {base['tool_calls']}"
    )
    ceiling = int(base["tokens"] * _TOKEN_REGRESSION_TOLERANCE)
    assert m["tokens"] <= ceiling, (
        f"{name}: token-per-task regression {m['tokens']} > {ceiling} "
        f"(baseline {base['tokens']}; regenerate with `make eval-baseline` if intended)"
    )


def test_baseline_covers_every_workflow() -> None:
    # The committed baseline must not silently omit a workflow (which would skip its gate).
    assert set(_BASELINE) == set(ALL_WORKFLOWS)


async def test_calibration_dedup_keeps_compact_lean(
    respx_router: respx.Router, facade: Any, structured: Any
) -> None:
    # GAP-1 lock: on a calibrated set the compact single-variant lookup is strictly
    # leaner than full, and standard sits between -- no cliff, no duplicated ladder.
    mock_surface(respx_router)

    async def tokens(mode: str) -> int:
        res = await facade.call_tool(
            "get_variant_score", {"urn": fixtures_variant_urn(), "response_mode": mode}
        )
        return int(structured(res)["_meta"]["token_estimate"])

    compact, standard, full = (
        await tokens("compact"),
        await tokens("standard"),
        await tokens("full"),
    )
    assert compact < standard < full, f"compact={compact} standard={standard} full={full}"


def fixtures_variant_urn() -> str:
    from tests import fixtures

    return fixtures.VARIANT_URN
