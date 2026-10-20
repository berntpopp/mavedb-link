"""An unchanged monthly dump must not request data-release approval."""

from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]


def _step(job: dict[str, object], name: str) -> object:
    steps = job["steps"]
    assert isinstance(steps, list)
    return next(step for step in steps if step.get("name") == name)


PUBLISH_IF = (
    "!cancelled() && needs.build.result == 'success' && github.ref == 'refs/heads/main' && "
    "needs.precheck.outputs.state != 'published_noop'"
)
READ_ONLY_IDENTITY_STEPS = (
    "Verify transferred bytes",
    "Inspect exact Git tag target",
    "Inventory exact release identity",
    "Inspect and verify an existing same-tag release",
    "Resolve the exact release identity state",
)


def test_unchanged_release_is_detected_before_the_gate_and_skips_publish() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/data.yml").read_text(encoding="utf-8"))
    precheck = workflow["jobs"]["precheck"]
    publish = workflow["jobs"]["publish"]
    # Ungated and read-only: no environment, no write scope.
    assert precheck["needs"] == "build"
    assert "environment" not in precheck
    assert precheck["permissions"] == {"contents": "read"}
    assert precheck["outputs"] == {"state": "${{ steps.decision.outputs.state }}"}
    # Only a verified identical published release skips the gate; every other state,
    # or a failed precheck (empty output), still reaches the required reviewer.
    assert publish["needs"] == ["build", "precheck"]
    assert publish["environment"] == "data-release"
    assert publish["if"] == PUBLISH_IF
    # Defence in depth: the gated job re-runs the identical read-only verification.
    for name in READ_ONLY_IDENTITY_STEPS:
        assert _step(precheck, name) == _step(publish, name)
    mutating = [s for s in precheck["steps"] if s.get("name") not in READ_ONLY_IDENTITY_STEPS]
    assert [s.get("uses", "").split("@")[0] for s in mutating] == ["actions/download-artifact"]
