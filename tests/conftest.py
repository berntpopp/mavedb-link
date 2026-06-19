"""Shared test fixtures: a respx-backed MaveDB client, service, and facade.

The MCP surface tests run the REAL stack (client -> service -> shaping ->
envelope -> next_commands); only the HTTP layer is mocked with respx. Routes are
registered per-test via the ``mock_routes`` helper.
"""

from __future__ import annotations

import json
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
    return json.loads(result.content[0].text)


@pytest.fixture
def structured() -> Any:
    """Expose the structured-content reader to tests."""
    return _structured


@pytest.fixture(autouse=True)
def _reset_metrics() -> Any:
    """Reset process-wide metrics between tests for deterministic snapshots."""
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def api_config() -> MaveDBApiConfig:
    """API config pointed at the test base URL, with caching disabled."""
    return MaveDBApiConfig(base_url=fixtures.BASE_URL, cache_ttl=0, cache_size=0, max_retries=2)


@pytest.fixture
async def client(api_config: MaveDBApiConfig) -> Any:
    """A real MaveDBClient (its httpx calls are intercepted by respx)."""
    c = MaveDBClient(api_config)
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
