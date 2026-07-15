"""Shared test fixtures: a respx-backed MaveDB client, service, and facade.

The MCP surface tests run the REAL stack (client -> service -> shaping ->
envelope -> next_commands); only the HTTP layer is mocked with respx. Routes are
registered per-test via the ``mock_routes`` helper.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.mcp import metrics
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures


def _structured(result: Any) -> dict[str, Any]:
    """Read structured_content defensively (with TextContent JSON fallback)."""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    loaded = json.loads(result.content[0].text)
    return loaded if isinstance(loaded, dict) else {}


@pytest.fixture
def structured() -> Any:
    """Expose the structured-content reader to tests."""
    return _structured


def _remove_cache_files(path: Path) -> None:
    """Remove a SQLite cache DB and its WAL sidecars if present."""
    path.unlink(missing_ok=True)
    Path(f"{path}-wal").unlink(missing_ok=True)
    Path(f"{path}-shm").unlink(missing_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _isolate_mapped_cache(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Point the global mapped-variant cache at pytest temp storage."""
    from mavedb_link.config import settings

    original = settings.cache.db_path
    settings.cache.db_path = tmp_path_factory.mktemp("mavedb-cache") / "mavedb_cache.sqlite"
    try:
        yield
    finally:
        _remove_cache_files(settings.cache.db_path)
        settings.cache.db_path = original


@pytest.fixture(autouse=True)
def _reset_process_state() -> Any:
    """Reset process-wide caches/metrics between tests for deterministic snapshots."""
    from mavedb_link.config import settings
    from mavedb_link.services.resolvers import (
        clear_gene_identity_cache,
        clear_hgvs_validation_cache,
    )

    _remove_cache_files(settings.cache.db_path)
    metrics.reset()
    clear_hgvs_validation_cache()
    clear_gene_identity_cache()
    yield
    _remove_cache_files(settings.cache.db_path)
    metrics.reset()
    clear_hgvs_validation_cache()
    clear_gene_identity_cache()


@pytest.fixture
def api_config() -> MaveDBApiConfig:
    """API config pointed at the test base URL, with caching disabled."""
    return MaveDBApiConfig(base_url=fixtures.BASE_URL, cache_ttl=0, cache_size=0, max_retries=2)


#: The corpus facet vocabulary a production mirror provides. Injected onto the
#: respx-backed client so faceted-search tests exercise the validated (mirror) path
#: with real values -- exactly the path production runs, since the mirror is always
#: present there. Covers every facet value the suite filters on.
_TEST_FACET_VOCAB: dict[str, set[str]] = {
    "targets": {"UBE2I", "SUMO1", "BRCA1", "BRCA2", "TP53"},
    "organisms": {"homo sapiens", "saccharomyces cerevisiae"},
    "authors": {"starita", "findlay", "weile", "bloom"},
}


@pytest.fixture
async def client(api_config: MaveDBApiConfig) -> Any:
    """A real MaveDBClient (its httpx calls are intercepted by respx).

    Carries ``facet_vocabularies`` so faceted-search tests validate against a corpus
    (the mirror path), rather than fail-closing as a mirror-less live-only client would.
    """
    c = MaveDBClient(api_config)
    c.facet_vocabularies = lambda: _TEST_FACET_VOCAB  # type: ignore[attr-defined]
    yield c
    await c.aclose()


@pytest.fixture
async def service(client: MaveDBClient) -> Any:
    """A service backed by the respx-intercepted client."""
    yield MaveDBService(client)


@pytest.fixture
async def facade(service: MaveDBService) -> Any:
    """A FastMCP facade with the respx-backed service injected; cleans up after."""
    from mavedb_link.mcp.facade import create_mavedb_mcp
    from mavedb_link.mcp.service_adapters import set_mavedb_service

    set_mavedb_service(service)
    mcp = create_mavedb_mcp()
    yield mcp
    set_mavedb_service(None)


@pytest.fixture
def respx_router() -> Any:
    """A respx router scoped to the test base URL (assert_all_called off)."""
    with respx.mock(base_url=fixtures.BASE_URL, assert_all_called=False) as router:
        yield router


def json_response(payload: Any, status_code: int = 200) -> httpx.Response:
    """Build a JSON httpx.Response for a respx route."""
    return httpx.Response(status_code, json=payload)


def csv_response(text: str, status_code: int = 200) -> httpx.Response:
    """Build a text/csv httpx.Response for a respx route."""
    return httpx.Response(status_code, text=text, headers={"content-type": "text/csv"})
