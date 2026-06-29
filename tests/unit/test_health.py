"""Unit test for /health endpoint — asserts Transport Standard v1 fields."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mavedb_link.app import create_app


def test_health_returns_status_version_transport() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["transport"] == "streamable-http-stateless"
