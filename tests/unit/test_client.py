"""Tests for the MaveDB HTTP client (status mapping, retry, caching, CSV)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.exceptions import (
    InvalidInputError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from tests import fixtures

BASE = fixtures.BASE_URL


@pytest.fixture
async def client() -> MaveDBClient:
    c = MaveDBClient(MaveDBApiConfig(base_url=BASE, cache_ttl=0, cache_size=0, max_retries=2))
    yield c
    await c.aclose()


@respx.mock(base_url=BASE)
async def test_get_json_ok(respx_mock: respx.Router, client: MaveDBClient) -> None:
    respx_mock.get("/genes/UBE2I").mock(return_value=httpx.Response(200, json={"symbol": "UBE2I"}))
    data = await client.get_json("/genes/UBE2I")
    assert data == {"symbol": "UBE2I"}


@respx.mock(base_url=BASE)
async def test_post_json_ok(respx_mock: respx.Router, client: MaveDBClient) -> None:
    route = respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(200, json={"numScoreSets": 0, "scoreSets": []})
    )
    data = await client.post_json("/score-sets/search", json={"text": "x"})
    assert data["numScoreSets"] == 0
    assert route.called


@respx.mock(base_url=BASE)
async def test_404_maps_not_found(respx_mock: respx.Router, client: MaveDBClient) -> None:
    respx_mock.get("/score-sets/missing").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    with pytest.raises(NotFoundError):
        await client.get_json("/score-sets/missing")


@respx.mock(base_url=BASE)
async def test_422_maps_invalid_input(respx_mock: respx.Router, client: MaveDBClient) -> None:
    respx_mock.post("/score-sets/search").mock(
        return_value=httpx.Response(422, json={"detail": [{"msg": "bad field"}]})
    )
    with pytest.raises(InvalidInputError):
        await client.post_json("/score-sets/search", json={})


@respx.mock(base_url=BASE)
async def test_429_retries_then_raises(respx_mock: respx.Router, client: MaveDBClient) -> None:
    route = respx_mock.get("/genes/X").mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(RateLimitError):
        await client.get_json("/genes/X")
    # max_retries=2 -> 3 attempts total
    assert route.call_count == 3


@respx.mock(base_url=BASE)
async def test_429_then_success(respx_mock: respx.Router, client: MaveDBClient) -> None:
    route = respx_mock.get("/genes/Y")
    route.side_effect = [
        httpx.Response(429, text="slow"),
        httpx.Response(200, json={"symbol": "Y"}),
    ]
    data = await client.get_json("/genes/Y")
    assert data == {"symbol": "Y"}
    assert route.call_count == 2


@respx.mock(base_url=BASE)
async def test_500_maps_unavailable(respx_mock: respx.Router, client: MaveDBClient) -> None:
    respx_mock.get("/genes/Z").mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(ServiceUnavailableError):
        await client.get_json("/genes/Z")


@respx.mock(base_url=BASE)
async def test_network_error_maps_unavailable(
    respx_mock: respx.Router, client: MaveDBClient
) -> None:
    respx_mock.get("/genes/Net").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ServiceUnavailableError):
        await client.get_json("/genes/Net")


@respx.mock(base_url=BASE)
async def test_get_text_csv(respx_mock: respx.Router, client: MaveDBClient) -> None:
    respx_mock.get("/score-sets/u/scores").mock(
        return_value=httpx.Response(200, text="a,b\n1,2\n", headers={"content-type": "text/csv"})
    )
    text = await client.get_text("/score-sets/u/scores")
    assert text.startswith("a,b")


async def test_cache_serves_repeat_get() -> None:
    cached = MaveDBClient(MaveDBApiConfig(base_url=BASE, cache_ttl=600, cache_size=16))
    try:
        with respx.mock(base_url=BASE) as mock:
            route = mock.get("/genes/Cached").mock(
                return_value=httpx.Response(200, json={"symbol": "Cached"})
            )
            await cached.get_json("/genes/Cached")
            await cached.get_json("/genes/Cached")
            assert route.call_count == 1  # second served from cache
    finally:
        await cached.aclose()


@respx.mock(base_url=BASE)
async def test_get_version(respx_mock: respx.Router, client: MaveDBClient) -> None:
    # MaveDB registers /api/version UNDER /api/v1 -> {base}/api/version
    route = respx_mock.get("/api/version").mock(
        return_value=httpx.Response(200, json=fixtures.API_VERSION_RESPONSE)
    )
    data = await client.get_version()
    assert data["version"] == "2026.2.4"
    assert route.called


@respx.mock
async def test_api_redirect_is_not_followed() -> None:
    target_url = "https://evil.example/api"
    target = respx.get(target_url).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{BASE}/api/version").mock(
        return_value=httpx.Response(302, headers={"Location": target_url})
    )
    redirecting = MaveDBClient(MaveDBApiConfig(base_url=BASE, max_retries=0))
    try:
        with pytest.raises(ServiceUnavailableError, match="redirect"):
            await redirecting.get_version()
        assert target.called is False
    finally:
        await redirecting.aclose()
