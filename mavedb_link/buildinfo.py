"""Build/version stamp so a running server can report its own provenance.

Provenance is injected by the Docker image build (``MAVEDB_LINK_GIT_SHA`` /
``MAVEDB_LINK_BUILT_AT``). In a source checkout those env vars are absent, so the
git sha is resolved from ``.git`` with a dependency-free reader and ``built_at``
falls back to the package mtime — the server can always say which build answered.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

from mavedb_link import __version__


def _git_sha_from_dotgit() -> str | None:
    """Resolve the current commit sha by reading ``.git`` (no subprocess)."""
    root = Path(__file__).resolve().parent.parent
    git = root / ".git"
    if not git.exists():
        return None
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:12]  # detached HEAD: raw sha
        ref = head[4:].strip()
        loose = git / ref
        if loose.exists():
            return loose.read_text(encoding="utf-8").strip()[:12]
        packed = git / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith(("#", "^")) and line.endswith(ref):
                    return line.split()[0][:12]
        return None
    except OSError:
        return None


def _source_tree_sha() -> str | None:
    """Deterministic 12-char hash of the installed package source.

    A reproducible provenance anchor when neither an injected sha nor a ``.git``
    is present (a non-git checkout) — honest as ``git_sha_source="source_tree"``.
    """
    package = Path(__file__).resolve().parent
    try:
        digest = hashlib.sha256()
        for path in sorted(package.rglob("*.py")):
            digest.update(path.relative_to(package).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
        return digest.hexdigest()[:12]
    except OSError:
        return None


def _resolve_git_sha() -> tuple[str, str]:
    """Return ``(git_sha, source)`` with source ∈ env|git|source_tree|unknown."""
    env_sha = os.environ.get("MAVEDB_LINK_GIT_SHA")
    if env_sha:
        return env_sha, "env"
    dotgit = _git_sha_from_dotgit()
    if dotgit:
        return dotgit, "git"
    source_sha = _source_tree_sha()
    if source_sha:
        return source_sha, "source_tree"
    return "unknown", "unknown"


def _built_at_fallback() -> str | None:
    """ISO-8601 mtime of the package as a best-effort build timestamp."""
    try:
        mtime = Path(__file__).with_name("__init__.py").stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    except OSError:
        return None


def build_info() -> dict[str, str | None]:
    """Return version + git sha (+ its source) + build time.

    ``git_sha`` resolves env-injected → ``.git`` → deterministic source-tree hash,
    so a running server can always report a real provenance anchor (DEF-9), and
    ``git_sha_source`` discloses which path produced it.
    """
    git_sha, git_sha_source = _resolve_git_sha()
    return {
        "version": __version__,
        "git_sha": git_sha,
        "git_sha_source": git_sha_source,
        "built_at": os.environ.get("MAVEDB_LINK_BUILT_AT") or _built_at_fallback(),
    }
