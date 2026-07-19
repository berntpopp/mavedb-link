from __future__ import annotations

import asyncio
import hashlib
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.asyncio
async def test_contract_truth_v1_matches_live_mavedb_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    helper = repo_root / "tests/conformance/contract_truth.py"
    pin = repo_root / "tests/conformance/contract_truth.sha256"

    assert (
        hashlib.sha256(helper.read_bytes()).hexdigest() == pin.read_text(encoding="utf-8").strip()
    )

    def deny_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("contract-truth registry discovery attempted outbound network access")

    async def deny_async_network(*_args: object, **_kwargs: object) -> None:
        deny_network()

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(asyncio, "open_connection", deny_async_network)
    monkeypatch.chdir(tmp_path)

    from mavedb_link.mcp.facade import create_mavedb_mcp
    from mavedb_link.mcp.service_adapters import reset_mavedb_service, set_mavedb_service
    from tests.conformance.contract_truth import (
        active_markdown_files,
        historical_markdown_files,
        lint_repository,
    )

    set_mavedb_service(Mock())
    try:
        tools = await create_mavedb_mcp().list_tools()
    finally:
        reset_mavedb_service()

    catalog = {tool.name: {"inputSchema": tool.parameters or {"properties": {}}} for tool in tools}

    assert tools
    assert active_markdown_files(repo_root)
    assert historical_markdown_files(repo_root)
    assert lint_repository(repo_root, catalog) == []
