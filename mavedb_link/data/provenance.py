"""Per-request data-source provenance (mirror vs live), via a contextvar.

The hybrid client records which backend answered each upstream read; the MCP
envelope reads a snapshot when assembling ``_meta`` so every response honestly
reports ``data_source`` (``mirror`` | ``live`` | ``mixed``) and the mirror's
``mirror_as_of`` snapshot date, plus the Response-Envelope v1.2 provenance
(``source``, ``data_version``, ``retrieved_at``). State lives in a single mutable dict bound once
per tool call, so concurrent reads (``asyncio.gather``) accumulate into the same
object and remain visible to the parent context.
"""

from __future__ import annotations

import contextvars
from datetime import UTC, datetime
from typing import Any

_state: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mavedb_data_provenance", default=None
)


def utc(moment: datetime | str | None = None) -> str:
    """An RFC 3339 UTC timestamp ending in ``Z`` (now, or a stored ISO time)."""
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    return (moment or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def begin() -> None:
    """Start a fresh provenance scope (call once at tool entry)."""
    _state.set({"sources": set(), "as_of": None, "version": None, "fetched": [], "start": utc()})


def record(
    source: str,
    *,
    mirror_as_of: str | None = None,
    data_version: str | None = None,
    retrieved_at: str | None = None,
) -> None:
    """Record that ``source`` answered a read (no-op outside a tool scope)."""
    state = _state.get()
    if state is None:
        return
    state["sources"].add(source)
    if mirror_as_of and not state["as_of"]:
        state["as_of"] = mirror_as_of
    if data_version and not state["version"]:
        state["version"] = data_version
    fetched(retrieved_at)


def fetched(retrieved_at: str | None) -> None:
    """Record the upstream fetch time of a cached or mirrored read (v1.2)."""
    state = _state.get()
    if state is not None and retrieved_at:
        state["fetched"].append(retrieved_at)


def snapshot() -> dict[str, Any]:
    """The provenance keys for ``_meta`` (empty outside a tool scope).

    ``retrieved_at`` is the oldest recorded upstream fetch; a call that recorded
    none read live during this request, so it reports the request start.
    """
    state = _state.get()
    if state is None:
        return {}
    out: dict[str, Any] = {
        "source": "mavedb",
        "data_version": state["version"] or "live",
        "retrieved_at": min(state["fetched"], default=state["start"]),
    }
    sources = state["sources"]
    if sources:
        out["data_source"] = "mixed" if len(sources) > 1 else next(iter(sources))
    if state["as_of"]:
        out["mirror_as_of"] = state["as_of"]
    return out
