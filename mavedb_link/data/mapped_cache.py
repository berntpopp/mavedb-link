"""On-disk write-through cache for live mapped-variant (VRS/ClinGen) results.

The CC0 MaveDB Zenodo bulk dump omits the per-set annotations CSVs, so the
mirror's VRS/ClinGen layer is empty. This cache backfills it **lazily**: the
first tool call that touches a score set fetches its mapped variants from the
live API and writes the raw list here; repeats then serve locally.

It follows the GeneFoundry fleet convention established by
``metadome-link/cache/store.py``: an SQLite store keyed by ``(id, data_version)``
(WAL, JSON-blob value, ``fetched_at``, UPSERT) with an in-memory LRU front. Disk
is authoritative; the LRU is pure acceleration. ``data_version`` ties freshness to
the mirror snapshot, so a refresh to a newer dump auto-invalidates stale rows.
"""

from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mavedb_link.constants import MAPPED_CACHE_LRU_SETS, MAPPED_CACHE_VERSION

#: A present row means "this set was enriched" -- even a stored ``[]`` (the set has
#: zero mappings) is a recorded result, distinct from "not yet fetched" (no row).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS mapped_variants (
    score_set_urn TEXT NOT NULL,
    data_version  TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    json          TEXT NOT NULL,
    PRIMARY KEY (score_set_urn, data_version)
);
"""

_Items = list[dict[str, Any]]


def mapped_cache_data_version(mirror_meta: dict[str, Any] | None) -> str:
    """Build the cache ``data_version`` from the mirror snapshot.

    ``f"{MAPPED_CACHE_VERSION}:{marker}"`` where ``marker`` is the mirror's Zenodo
    version (or dump date), else ``"live"``. A mirror refresh to a newer dump thus
    changes the version and transparently invalidates stale cache rows.
    """
    marker = "live"
    if mirror_meta:
        marker = str(mirror_meta.get("zenodo_version") or mirror_meta.get("dump_as_of") or "live")
    return f"{MAPPED_CACHE_VERSION}:{marker}"


class _LRU:
    """A tiny insertion/access-ordered LRU (no TTL: disk is authoritative)."""

    def __init__(self, maxsize: int) -> None:
        """Initialise with a maximum entry count (``<= 0`` disables the LRU)."""
        self._maxsize = maxsize
        self._store: OrderedDict[str, _Items] = OrderedDict()

    def get(self, key: str) -> _Items | None:
        """Return the cached value (moved to MRU), or ``None`` if absent."""
        if self._maxsize <= 0:
            return None
        value = self._store.get(key)
        if value is None:
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: _Items) -> None:
        """Insert/refresh a value, evicting the least-recently-used if full."""
        if self._maxsize <= 0:
            return
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._store.clear()

    @property
    def size(self) -> int:
        """Current number of entries."""
        return len(self._store)


class MappedVariantCache:
    """SQLite-backed store of raw live mapped-variant lists, with an LRU front.

    Keyed by ``(score_set_urn, data_version)``; the stored value is the raw live
    mapped-variant list (so every ``get_mapped_variants`` response mode is
    reproducible) and a present row marks the set enriched (``[]`` included).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        data_version: str,
        lru_sets: int = MAPPED_CACHE_LRU_SETS,
    ) -> None:
        """Open (creating if needed) the cache database and the LRU layer."""
        self._data_version = data_version
        self._lru = _LRU(lru_sets)
        self._closed = False
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def get(self, score_set_urn: str) -> _Items | None:
        """Return the cached raw mapped-variant list, or ``None`` on miss.

        Checks the in-memory LRU first, then SQLite (warming the LRU on a disk
        hit). A stored empty list returns ``[]`` -- a recorded result, not a miss.
        """
        cached = self._lru.get(score_set_urn)
        if cached is not None:
            return cached
        row = self._con.execute(
            "SELECT json FROM mapped_variants WHERE score_set_urn = ? AND data_version = ?",
            (score_set_urn, self._data_version),
        ).fetchone()
        if row is None:
            return None
        items: _Items = json.loads(row[0])
        self._lru.set(score_set_urn, items)
        return items

    def put(self, score_set_urn: str, items: _Items) -> None:
        """Store ``items`` for a score set (replacing any existing entry)."""
        blob = json.dumps(items, ensure_ascii=False)
        self._con.execute(
            "INSERT INTO mapped_variants (score_set_urn, data_version, fetched_at, json) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (score_set_urn, data_version) DO UPDATE "
            "SET fetched_at = excluded.fetched_at, json = excluded.json",
            (score_set_urn, self._data_version, datetime.now(UTC).isoformat(), blob),
        )
        self._con.commit()
        self._lru.set(score_set_urn, items)

    def is_cached(self, score_set_urn: str) -> bool:
        """Whether a set has been enriched for the current data version."""
        if self._lru.get(score_set_urn) is not None:
            return True
        return (
            self._con.execute(
                "SELECT 1 FROM mapped_variants WHERE score_set_urn = ? AND data_version = ?",
                (score_set_urn, self._data_version),
            ).fetchone()
            is not None
        )

    def stats(self) -> dict[str, Any]:
        """Summary: on-disk count for this version, LRU size, data version."""
        row = self._con.execute(
            "SELECT COUNT(*) FROM mapped_variants WHERE data_version = ?",
            (self._data_version,),
        ).fetchone()
        return {
            "on_disk": int(row[0]) if row else 0,
            "lru_size": self._lru.size,
            "data_version": self._data_version,
        }

    def clear(self) -> int:
        """Delete every entry for the current data version; return the row count."""
        cur = self._con.execute(
            "DELETE FROM mapped_variants WHERE data_version = ?",
            (self._data_version,),
        )
        self._con.commit()
        self._lru.clear()
        return cur.rowcount

    def close(self) -> None:
        """Close the SQLite connection (idempotent)."""
        if not self._closed:
            self._con.close()
            self._closed = True
