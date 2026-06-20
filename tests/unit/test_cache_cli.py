"""mavedb-link-cache CLI for mapped-variant cache maintenance."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mavedb_link.config import settings
from mavedb_link.data.mapped_cache import MappedVariantCache


@pytest.fixture
def cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "mapped.sqlite"
    monkeypatch.setattr(settings.cache, "enabled", True)
    monkeypatch.setattr(settings.cache, "db_path", path)
    monkeypatch.setattr(settings.cache, "lru_sets", 4)
    monkeypatch.setattr(settings.mirror, "enabled", False)
    return path


def test_status_prints_cache_stats(cache_path: Path) -> None:
    from mavedb_link.data.cache_cli import app

    cache = MappedVariantCache(cache_path, data_version="1:live", lru_sets=4)
    cache.put("urn:mavedb:1-a-1", [])
    cache.close()

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "data_version=1:live" in result.output
    assert "on_disk=1" in result.output
    assert "lru_size=0" in result.output


def test_clear_yes_empties_cache(cache_path: Path) -> None:
    from mavedb_link.data.cache_cli import app

    cache = MappedVariantCache(cache_path, data_version="1:live", lru_sets=4)
    cache.put("urn:mavedb:1-a-1", [{"variantUrn": "urn:mavedb:1-a-1#1"}])
    cache.close()

    result = CliRunner().invoke(app, ["clear", "--yes"])

    assert result.exit_code == 0
    assert "cleared=1" in result.output
    check = MappedVariantCache(cache_path, data_version="1:live", lru_sets=4)
    try:
        assert check.get("urn:mavedb:1-a-1") is None
    finally:
        check.close()
