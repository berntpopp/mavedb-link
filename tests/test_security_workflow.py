"""CI-hardening regression: the security workflow must exist and be blocking.

Finding F-18: this repo lacked CodeQL and dependency-review coverage. The
``.github/workflows/security.yml`` workflow adds both, with least-privilege
permissions, SHA-pinned actions, a pull-request trigger, and a BLOCKING
high-severity dependency policy (no ``continue-on-error``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "security.yml"

#: A commit-pinned action ref: ``owner/repo@<40-hex-sha>`` (optionally sub-path).
_SHA_PIN = re.compile(r"@[0-9a-f]{40}\b")


def _load() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW.read_text())


def _triggers(doc: dict[str, Any]) -> Any:
    # PyYAML parses the bare key ``on:`` as the boolean ``True`` (YAML 1.1), so
    # accept either spelling.
    return doc.get("on", doc.get(True))


def test_security_workflow_parses() -> None:
    assert _WORKFLOW.exists(), "security.yml must exist"
    doc = _load()
    assert isinstance(doc, dict)


def test_security_workflow_runs_on_pull_request() -> None:
    triggers = _triggers(_load())
    # ``on:`` may be a list, a mapping, or a bare string; normalise to text.
    assert "pull_request" in str(triggers)


def test_security_workflow_has_least_privilege_permissions() -> None:
    doc = _load()
    assert doc.get("permissions") == {"contents": "read"}


def test_codeql_and_dependency_review_jobs_present() -> None:
    jobs = _load()["jobs"]
    assert "codeql" in jobs
    assert "dependency-review" in jobs


def test_workflow_actions_are_sha_pinned() -> None:
    text = _WORKFLOW.read_text()
    uses = [ln.split("uses:", 1)[1].strip() for ln in text.splitlines() if "uses:" in ln]
    assert uses, "workflow must invoke actions"
    for ref in uses:
        assert _SHA_PIN.search(ref), f"action not SHA-pinned: {ref}"


def test_dependency_review_is_blocking_high_severity() -> None:
    review = _load()["jobs"]["dependency-review"]
    steps = review["steps"]
    dep_step = next(s for s in steps if "dependency-review-action" in str(s.get("uses", "")))
    assert "continue-on-error" not in dep_step, "dependency-review must be blocking"
    assert dep_step.get("with", {}).get("fail-on-severity") == "high"
