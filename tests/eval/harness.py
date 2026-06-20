"""Eval harness: a mocked-facade recorder + the realistic multi-call workflows.

Anthropic's strongest lever for keeping a tool surface above 9.5 is evaluation-
driven iteration (A8): realistic multi-call tasks with verifiable outcomes, scored
on accuracy, tool-call count, token consumption, and error rate. This harness runs
the FULL stack (client -> service -> shaping -> envelope -> next_commands) against
deterministic respx fixtures, so the metrics are stable enough to gate CI.

The same workflows back both the regression test (tests/eval/test_eval_regression)
and the baseline regenerator (scripts/eval_baseline.py).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import respx

from tests import fixtures

#: A second-score-set variant record so the cross-dataset rollup enriches both hits.
_VARIANT_SET2 = f"{fixtures.SCORE_SET_URN_2}#5"
_VARIANT_SET2_ENCODED = _VARIANT_SET2.replace("#", "%23")
_VARIANT_RAW_SET2: dict[str, Any] = {
    "urn": _VARIANT_SET2,
    "hgvsNt": "c.5A>G",
    "scoreSet": {"urn": fixtures.SCORE_SET_URN_2},
    "data": {"score_data": {"score": 0.1}},
    "mappedVariants": [{"postMapped": {"id": fixtures.VRS_ID}, "current": True}],
}


def read_structured(result: Any) -> dict[str, Any]:
    """Read a ToolResult's structured content (TextContent JSON fallback)."""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    return json.loads(result.content[0].text)  # type: ignore[no-any-return]


def mock_surface(router: respx.Router) -> None:
    """Register the full upstream route surface used by the eval workflows."""
    router.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SETS_SEARCH_RESPONSE)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_WITH_CALIBRATIONS_RAW)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN_2}").mock(
        return_value=httpx.Response(200, json=fixtures.SCORE_SET_RAW_2)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/scores").mock(
        return_value=httpx.Response(200, text=fixtures.SCORES_CSV)
    )
    router.get(f"/score-sets/{fixtures.SCORE_SET_URN}/mapped-variants").mock(
        return_value=httpx.Response(200, json=fixtures.MAPPED_VARIANTS_RAW)
    )
    router.get(f"/variants/{fixtures.VARIANT_URN_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VARIANT_RAW)
    )
    router.get(f"/variants/{_VARIANT_SET2_ENCODED}").mock(
        return_value=httpx.Response(200, json=_VARIANT_RAW_SET2)
    )
    router.get("/genes/UBE2I").mock(return_value=httpx.Response(200, json=fixtures.GENE_RESPONSE))
    router.get(f"/experiments/{fixtures.EXPERIMENT_URN}").mock(
        return_value=httpx.Response(200, json=fixtures.EXPERIMENT_RAW)
    )
    router.post("/experiments/search").mock(
        return_value=httpx.Response(200, json=fixtures.EXPERIMENTS_SEARCH_RESPONSE)
    )
    router.get("/api/version").mock(
        return_value=httpx.Response(200, json=fixtures.API_VERSION_RESPONSE)
    )
    router.get(f"/mapped-variants/vrs/{fixtures.VRS_ID_ENCODED}").mock(
        return_value=httpx.Response(200, json=fixtures.VRS_CROSS_DATASET_RAW)
    )
    router.get(f"/score-calibrations/score-set/{fixtures.SCORE_SET_URN}/primary").mock(
        return_value=httpx.Response(200, json=fixtures.PRIMARY_CALIBRATION_RAW)
    )
    router.get(f"/score-calibrations/{fixtures.CALIBRATION_URN}/variants").mock(
        return_value=httpx.Response(200, json=fixtures.CALIBRATION_VARIANTS_RAW)
    )


@dataclass
class Recorder:
    """Drives tool calls through the facade and tallies the eval metrics."""

    facade: Any
    structured: Callable[[Any], dict[str, Any]]
    tool_calls: int = 0
    tokens: int = 0
    errors: int = 0
    checks: int = 0
    checks_passed: int = 0
    _trace: list[str] = field(default_factory=list)

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool, tally tool-call count / tokens / errors, return the payload."""
        self.tool_calls += 1
        payload = self.structured(await self.facade.call_tool(tool, args))
        meta = payload.get("_meta") or {}
        self.tokens += int(meta.get("token_estimate") or 0)
        if not payload.get("success", False):
            self.errors += 1
        self._trace.append(tool)
        return payload

    def check(self, ok: bool, label: str) -> None:
        """Record one verifiable-outcome assertion (accuracy)."""
        self.checks += 1
        if ok:
            self.checks_passed += 1
        else:  # surface which accuracy check failed
            self._trace.append(f"FAILED CHECK: {label}")

    def metrics(self) -> dict[str, Any]:
        """The per-workflow metric vector (the eval's verifiable signal)."""
        return {
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "errors": self.errors,
            "accuracy": round(self.checks_passed / self.checks, 4) if self.checks else 1.0,
        }


Workflow = Callable[[Recorder], Awaitable[None]]


def report(metrics: dict[str, dict[str, Any]]) -> str:
    """Render a metrics table (used by the baseline regenerator)."""
    return json.dumps(metrics, indent=2, sort_keys=True)
