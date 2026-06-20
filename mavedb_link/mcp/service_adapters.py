"""Lazily-constructed singleton MaveDBService for the MCP tools.

The service wraps one shared :class:`MaveDBClient` (a single ``httpx.AsyncClient``
reused across tool calls). It is built on first use; tests inject a fake via
:func:`set_mavedb_service`.
"""

from __future__ import annotations

import logging

from mavedb_link.api.client import MaveDBClient
from mavedb_link.data.hybrid import HybridClient
from mavedb_link.data.repository import MirrorRepository
from mavedb_link.services.mavedb_service import MaveDBService

logger = logging.getLogger(__name__)

_service: MaveDBService | None = None


def _build_service() -> MaveDBService:
    """Build the service over the mirror-first client when a built DB exists.

    Falls back to the pure live client when the mirror is disabled or absent, so
    a missing/unbuilt database never blocks startup (the live API is the backup).
    """
    from mavedb_link.config import settings

    if settings.mirror.enabled:
        repo = MirrorRepository.open(settings.mirror.db_path)
        if repo is not None:
            meta = repo.meta()
            logger.info(
                "mirror enabled db=%s as_of=%s score_sets=%s",
                settings.mirror.db_path,
                meta.get("dump_as_of"),
                meta.get("score_set_count"),
            )
            return MaveDBService(HybridClient(settings.api, repository=repo))
        logger.info(
            "mirror enabled but no database at %s; serving live-only", settings.mirror.db_path
        )
    return MaveDBService(MaveDBClient(settings.api))


def get_mavedb_service() -> MaveDBService:
    """Return a process-wide :class:`MaveDBService` (built on first use)."""
    global _service
    if _service is None:
        _service = _build_service()
    return _service


def set_mavedb_service(service: MaveDBService | None) -> None:
    """Override the singleton (used by tests)."""
    global _service
    _service = service


def reset_mavedb_service() -> None:
    """Drop the cached service so the next call rebuilds it."""
    global _service
    _service = None


async def close_mavedb_service() -> None:
    """Close and drop the cached service (server shutdown)."""
    global _service
    if _service is not None:
        await _service.aclose()
        _service = None
