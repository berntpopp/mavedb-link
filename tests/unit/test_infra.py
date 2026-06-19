"""Tests for infrastructure modules: logging, buildinfo, service adapter."""

from __future__ import annotations

from mavedb_link.buildinfo import build_info
from mavedb_link.logging_config import configure_logging
from mavedb_link.mcp import service_adapters


def test_configure_logging_returns_logger() -> None:
    logger = configure_logging()
    assert logger is not None
    logger.info("test event", marker=True)


def test_build_info_shape() -> None:
    info = build_info()
    assert info["version"]
    assert "git_sha" in info
    assert "built_at" in info


def test_build_info_git_sha_is_real_and_labeled() -> None:
    # DEF-9: provenance must never be a bare "unknown" in a real source tree —
    # fall back to a deterministic source-tree hash, honestly labeled.
    info = build_info()
    assert info["git_sha"] not in (None, "", "unknown")
    assert info["git_sha_source"] in ("env", "git", "source_tree")


def test_build_info_source_sha_is_deterministic() -> None:
    assert build_info()["git_sha"] == build_info()["git_sha"]


def test_service_adapter_singleton_and_override() -> None:
    sentinel = object()
    service_adapters.set_mavedb_service(sentinel)  # type: ignore[arg-type]
    assert service_adapters.get_mavedb_service() is sentinel
    service_adapters.reset_mavedb_service()
    # After reset a real service is built lazily on next access.
    built = service_adapters.get_mavedb_service()
    assert built is not sentinel
    service_adapters.set_mavedb_service(None)


async def test_close_mavedb_service_is_safe_when_unset() -> None:
    service_adapters.set_mavedb_service(None)
    await service_adapters.close_mavedb_service()  # no-op, must not raise
