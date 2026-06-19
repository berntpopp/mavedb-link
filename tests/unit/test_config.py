"""Tests for configuration loading and validators."""

from __future__ import annotations

from mavedb_link.config import MaveDBApiConfig, ServerSettings


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
