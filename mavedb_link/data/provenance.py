"""Per-request data-source provenance (mirror vs live), via a contextvar.

The hybrid client records which backend answered each upstream read; the MCP
envelope reads a snapshot when assembling ``_meta`` so every response honestly
reports ``data_source`` (``mirror`` | ``live`` | ``mixed``) and the mirror's
``mirror_as_of`` snapshot date. State lives in a single mutable dict bound once
per tool call, so concurrent reads (``asyncio.gather``) accumulate into the same
object and remain visible to the parent context.
"""

from __future__ import annotations

import contextvars
from typing import Any

_state: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mavedb_data_provenance", default=None
)


def begin() -> None:
    """Start a fresh provenance scope (call once at tool entry)."""
    _state.set({"sources": set(), "as_of": None})


def record(source: str, *, mirror_as_of: str | None = None) -> None:
    """Record that ``source`` answered a read (no-op outside a tool scope)."""
    state = _state.get()
    if state is None:
        return
    state["sources"].add(source)
    if mirror_as_of and not state["as_of"]:
        state["as_of"] = mirror_as_of


def snapshot() -> dict[str, Any]:
    """The provenance keys for ``_meta`` (empty when nothing was recorded)."""
    state = _state.get()
    if not state or not state["sources"]:
        return {}
    sources = state["sources"]
    out: dict[str, Any] = {"data_source": "mixed" if len(sources) > 1 else next(iter(sources))}
    if state["as_of"]:
        out["mirror_as_of"] = state["as_of"]
    return out
