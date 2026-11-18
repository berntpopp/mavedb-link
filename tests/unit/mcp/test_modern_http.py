"""MCP over the real unified HTTP app: 2026-07-28 sessionless era + legacy handshake.

Drives ``server/discover`` -> ``tools/list`` -> ``tools/call`` with the modern
``mcp-method`` / ``mcp-name`` headers through the Host/Origin guards, proves an
unknown hostile tool name is answered with the fixed not-found envelope, and that
an unknown JSON-RPC METHOD is never echoed in the METHOD_NOT_FOUND frame (both eras).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mavedb_link.config import settings
from mavedb_link.server_manager import create_unified_app

_MODERN_VERSION = "2026-07-28"
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "mavedb-host-test", "version": "1.0.0"},
}
_HOSTILE_METHOD = "evil__IGNORE_ALL_PREVIOUS__method"
_BASE_HEADERS = {
    "host": "localhost",
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "allowed_hosts", ["localhost"])
    with TestClient(create_unified_app(), base_url="http://localhost") as test_client:
        yield test_client


def _modern(client: TestClient, rid: int, method: str, params: dict[str, Any]) -> Any:
    headers = {**_BASE_HEADERS, "mcp-protocol-version": _MODERN_VERSION, "mcp-method": method}
    if "name" in params:
        headers["mcp-name"] = params["name"]
    body = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": method,
        "params": {"_meta": _MODERN_META, **params},
    }
    return client.post("/mcp", headers=headers, json=body)


def _assert_method_not_found_clean(response: Any) -> None:
    error = response.json()["error"]
    assert error["code"] == -32601
    assert "data" not in error or error["data"] is None
    assert "IGNORE_ALL_PREVIOUS" not in response.text
    assert "evil" not in response.text


def test_modern_mcp_http_discovers_lists_calls_and_fences_unknown_tool(client: TestClient) -> None:
    hostile = "ignore-instructions-secret"
    discover = _modern(client, 1, "server/discover", {})
    listed = _modern(client, 2, "tools/list", {})
    called = _modern(client, 3, "tools/call", {"name": "get_server_capabilities", "arguments": {}})
    unknown = _modern(client, 4, "tools/call", {"name": hostile, "arguments": {}})

    assert discover.status_code == 200, discover.text
    assert _MODERN_VERSION in discover.json()["result"]["supportedVersions"]
    assert "mcp-session-id" not in discover.headers
    assert listed.status_code == 200
    assert "get_server_capabilities" in {t["name"] for t in listed.json()["result"]["tools"]}
    assert called.status_code == 200
    called_result = called.json()["result"]
    assert called_result.get("isError") in (None, False)
    assert json.loads(called_result["content"][0]["text"])["success"] is True
    assert unknown.status_code == 200
    unknown_result = unknown.json()["result"]
    assert unknown_result["isError"] is True
    assert unknown_result["structuredContent"]["error_code"] == "not_found"
    assert hostile not in unknown.text


def test_unknown_method_not_reflected_modern_era(client: TestClient) -> None:
    _assert_method_not_found_clean(_modern(client, 9, _HOSTILE_METHOD, {}))


def test_unknown_method_not_reflected_legacy_era(client: TestClient) -> None:
    init = client.post(
        "/mcp",
        headers=_BASE_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mavedb-host-test", "version": "1.0.0"},
            },
        },
    )
    assert init.status_code == 200, init.text
    headers = {**_BASE_HEADERS, "mcp-protocol-version": "2025-06-18"}
    response = client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": _HOSTILE_METHOD}
    )
    _assert_method_not_found_clean(response)
