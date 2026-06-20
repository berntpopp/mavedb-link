"""Test isolation for the on-disk mapped-variant cache."""

from __future__ import annotations

from pathlib import Path

from mavedb_link.config import settings


def test_tests_do_not_use_default_cache_path() -> None:
    assert settings.cache.db_path != Path("data/mavedb_cache.sqlite")
    assert "pytest" in str(settings.cache.db_path)
