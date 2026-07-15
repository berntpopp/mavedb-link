#!/usr/bin/env python3
"""Enforce a per-file line budget to keep modules focused and reviewable.

Run via `make lint-loc`. Fails (exit 1) if any tracked Python file exceeds the
soft cap, unless it is grandfathered in `.loc-allowlist` (``path:ceiling``).
A module that has grown too large gets split rather than sprawling.
"""

from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_LIMIT = 600
ROOTS = ("mavedb_link", "tests")
EXTRA_FILES = ("server.py", "mcp_server.py")
ALLOWLIST_FILE = ".loc-allowlist"
EXEMPT_PATHS = {
    # Vendored byte-identical from genefoundry-router; do not split or edit locally.
    "tests/conformance/behaviour.py",
}


def _load_allowlist(repo: Path) -> dict[str, int]:
    """Parse ``path:ceiling`` grandfather entries (blank/`#` lines ignored)."""
    path = repo / ALLOWLIST_FILE
    ceilings: dict[str, int] = {}
    if not path.exists():
        return ceilings
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        rel, _, ceiling = line.rpartition(":")
        try:
            ceilings[rel.strip()] = int(ceiling.strip())
        except ValueError:
            continue
    return ceilings


def main() -> int:
    """Report files over the line budget; return non-zero if any are found."""
    repo = Path(__file__).resolve().parents[1]
    allowlist = _load_allowlist(repo)
    offenders: list[tuple[Path, int, int]] = []
    paths: list[Path] = [repo / f for f in EXTRA_FILES]
    for root in ROOTS:
        paths.extend((repo / root).rglob("*.py"))
    for path in paths:
        if not path.exists():
            continue
        rel = str(path.relative_to(repo))
        if rel in EXEMPT_PATHS:
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        ceiling = allowlist.get(rel, DEFAULT_LIMIT)
        if lines > ceiling:
            offenders.append((path.relative_to(repo), lines, ceiling))
    for rel, lines, ceiling in sorted(offenders):
        print(f"{rel}: {lines} lines (> {ceiling})")
    if offenders:
        print(f"\n{len(offenders)} file(s) exceed the line budget.")
        return 1
    print(f"OK: all files within the {DEFAULT_LIMIT}-line budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
