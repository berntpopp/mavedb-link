"""Tests for configuration loading and validators."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mavedb_link.config import MaveDBApiConfig, ServerSettings

_SHA = "a" * 64
_EXPANDED_SHA = "b" * 64


def _production_settings(**mirror_overrides: object) -> ServerSettings:
    mirror: dict[str, object] = {
        "data_dir": Path("/reference/current"),
        "reference_root": Path("/reference"),
        "bundle_url": (
            "https://github.com/berntpopp/mavedb-link/releases/download/"
            "data-2026-07-01/mavedb.sqlite.zst"
        ),
        "bundle_release_tag": "data-2026-07-01",
        "bundle_expected_sha256": _SHA,
        "bundle_expected_expanded_sha256": _EXPANDED_SHA,
        "bundle_expected_schema_version": "4.0.0",
    }
    mirror.update(mirror_overrides)
    return ServerSettings(
        environment="production",
        mirror=mirror,
        cache={"db_path": Path("/cache/mavedb_cache.sqlite")},
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"bundle_url": "latest"}, "latest"),
        ({"bundle_release_tag": None}, "release tag"),
        ({"bundle_expected_sha256": None}, "compressed SHA-256"),
        ({"bundle_expected_expanded_sha256": None}, "expanded SHA-256"),
    ],
)
def test_production_requires_exact_mirror_identity(
    override: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _production_settings(**override)


def test_production_rejects_shared_reference_and_cache_path() -> None:
    with pytest.raises(ValidationError, match="cache path"):
        ServerSettings(
            environment="production",
            mirror={
                "data_dir": Path("/reference/current"),
                "reference_root": Path("/reference"),
                "bundle_url": (
                    "https://github.com/berntpopp/mavedb-link/releases/download/"
                    "data-2026-07-01/mavedb.sqlite.zst"
                ),
                "bundle_release_tag": "data-2026-07-01",
                "bundle_expected_sha256": _SHA,
                "bundle_expected_expanded_sha256": _EXPANDED_SHA,
                "bundle_expected_schema_version": "4.0.0",
            },
            cache={"db_path": Path("/reference/mavedb_cache.sqlite")},
        )


def test_development_latest_is_explicit_opt_in() -> None:
    with pytest.raises(ValidationError, match="development_latest"):
        ServerSettings(mirror={"bundle_url": "latest"})
    settings = ServerSettings(mirror={"bundle_url": "latest", "development_latest": True})
    assert settings.mirror.bundle_url == "latest"


def test_defaults() -> None:
    s = ServerSettings()
    assert s.transport == "unified"
    assert s.mcp_path == "/mcp"
    assert s.api.base_url == "https://api.mavedb.org/api/v1"
    assert s.api.max_concurrency >= 1


def test_mcp_path_gets_leading_slash() -> None:
    s = ServerSettings(mcp_path="mcp")
    assert s.mcp_path == "/mcp"


def test_cors_origins_from_csv() -> None:
    s = ServerSettings(cors_origins="http://a.test, http://b.test")
    assert s.cors_origins == ["http://a.test", "http://b.test"]


def test_api_base_url_trailing_slash_stripped() -> None:
    c = MaveDBApiConfig(base_url="https://api.mavedb.org/api/v1/")
    assert c.base_url == "https://api.mavedb.org/api/v1"


def test_env_prefix_override(monkeypatch: object) -> None:
    monkeypatch.setenv("MAVEDB_LINK_API__BASE_URL", "https://example.test/api/v1")  # type: ignore[attr-defined]
    monkeypatch.setenv("MAVEDB_LINK_PORT", "9100")  # type: ignore[attr-defined]
    s = ServerSettings()
    assert s.api.base_url == "https://example.test/api/v1"
    assert s.port == 9100


def test_cache_defaults() -> None:
    s = ServerSettings()
    assert s.cache.enabled is True
    assert s.cache.db_path == Path("data/mavedb_cache.sqlite")
    assert isinstance(s.cache.lru_sets, int) and s.cache.lru_sets > 0


def test_cache_env_override(monkeypatch: object) -> None:
    monkeypatch.setenv("MAVEDB_LINK_CACHE__ENABLED", "false")  # type: ignore[attr-defined]
    monkeypatch.setenv("MAVEDB_LINK_CACHE__LRU_SETS", "8")  # type: ignore[attr-defined]
    s = ServerSettings()
    assert s.cache.enabled is False
    assert s.cache.lru_sets == 8
