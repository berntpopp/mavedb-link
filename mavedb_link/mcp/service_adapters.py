"""Lazily-constructed singleton MaveDBService for the MCP tools.

The service wraps one shared :class:`MaveDBClient` (a single ``httpx.AsyncClient``
reused across tool calls). It is built on first use; tests inject a fake via
:func:`set_mavedb_service`.
"""

from __future__ import annotations

from mavedb_link.api.client import MaveDBClient
from mavedb_link.services.mavedb_service import MaveDBService

_service: MaveDBService | None = None


def _build_service() -> MaveDBService:
    from mavedb_link.config import settings

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
