"""On-disk MappedVariantCache (metadome ResultCache pattern): SQLite + LRU front."""

from __future__ import annotations

from pathlib import Path

from mavedb_link.data.mapped_cache import MappedVariantCache


def _cache(tmp_path: Path, version: str = "1:v4", lru: int = 16) -> MappedVariantCache:
    return MappedVariantCache(tmp_path / "cache.sqlite", data_version=version, lru_sets=lru)


def test_put_get_roundtrip(tmp_path: Path) -> None:
    c = _cache(tmp_path)
    c.put("urn:mavedb:1-a-1", [{"variantUrn": "x", "current": True}])
    assert c.get("urn:mavedb:1-a-1") == [{"variantUrn": "x", "current": True}]
    assert c.get("urn:mavedb:9-a-9") is None
    c.close()


def test_empty_list_is_enriched_not_miss(tmp_path: Path) -> None:
    c = _cache(tmp_path)
    c.put("s", [])
    assert c.get("s") == []  # an empty list is a recorded result, NOT a miss
    assert c.is_cached("s") is True
    assert c.is_cached("nope") is False
    c.close()


def test_data_version_invalidates(tmp_path: Path) -> None:
    p = tmp_path / "cache.sqlite"
    a = MappedVariantCache(p, data_version="1:v4")
    a.put("s", [{"a": 1}])
    a.close()
    b = MappedVariantCache(p, data_version="1:v5")  # different snapshot -> miss
    assert b.get("s") is None
    assert b.is_cached("s") is False
    b.close()


def test_lru_serves_without_disk(tmp_path: Path) -> None:
    c = _cache(tmp_path, lru=4)
    c.put("s", [{"a": 1}])
    assert c.get("s") == [{"a": 1}]  # warms LRU
    c._con.execute("DELETE FROM mapped_variants")  # nuke disk; LRU should still serve
    c._con.commit()
    assert c.get("s") == [{"a": 1}]
    c.close()


def test_stats_and_clear(tmp_path: Path) -> None:
    c = _cache(tmp_path)
    c.put("a", [{"x": 1}])
    c.put("b", [])
    s = c.stats()
    assert s["on_disk"] == 2
    assert s["data_version"] == "1:v4"
    assert c.clear() == 2
    assert c.get("a") is None
    assert c.stats()["on_disk"] == 0
    c.close()


def test_wal_mode_and_close_idempotent(tmp_path: Path) -> None:
    c = _cache(tmp_path)
    assert c._con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    c.close()
    c.close()  # idempotent, no error
