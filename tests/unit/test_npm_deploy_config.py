"""Deployment config contract for the NPM-hosted GeneFoundry fleet."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_npm_compose_overlay_matches_gene_foundry_fleet_contract() -> None:
    compose = ROOT / "docker" / "docker-compose.npm.yml"

    assert compose.exists()
    text = compose.read_text(encoding="utf-8")

    assert "name: mavedb-link-npm" in text
    assert "container_name: mavedb-link-npm" in text
    assert 'expose:\n      - "8000"' in text
    assert "MAVEDB_LINK_TRANSPORT: unified" in text
    assert "MAVEDB_LINK_MCP_PATH: /mcp" in text
    assert "MAVEDB_LINK_MIRROR__DATA_DIR: /home/app/data" in text
    assert "${NPM_SHARED_NETWORK_NAME:-npm_default}" in text
    assert "ports:" not in text


def test_env_docker_example_documents_npm_runtime_knobs() -> None:
    env_example = ROOT / ".env.docker.example"

    assert env_example.exists()
    text = env_example.read_text(encoding="utf-8")

    assert "NPM_SHARED_NETWORK_NAME=npm_default" in text
    assert "LOG_LEVEL_API=INFO" in text
    assert "MAVEDB_API_MEMORY_LIMIT=1g" in text
    assert "MAVEDB_API_CPU_LIMIT=1.0" in text
