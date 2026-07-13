"""Deployment config contract for the NPM-hosted GeneFoundry fleet."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_vendored_data_contract_matches_recorded_hash() -> None:
    schema = ROOT / "vendor/genefoundry/data-release-manifest.schema.json"
    recorded = (ROOT / "vendor/genefoundry/CONTRACT_SHA256").read_text().strip()
    assert hashlib.sha256(schema.read_bytes()).hexdigest() == recorded


def test_data_workflow_is_draft_first_and_non_overwriting() -> None:
    workflow = (ROOT / ".github/workflows/data.yml").read_text()
    assert "build:" in workflow and "publish:" in workflow
    assert "draft=true" in workflow
    assert "actions/attest-build-provenance@43d14" in workflow
    assert "gh release verify-asset" in workflow
    assert "--clobber" not in workflow


def test_npm_compose_overlay_matches_gene_foundry_fleet_contract() -> None:
    compose = ROOT / "docker" / "docker-compose.npm.yml"

    assert compose.exists()
    text = compose.read_text(encoding="utf-8")

    assert "name: mavedb-link-npm" in text
    assert "container_name: mavedb-link-npm" in text
    assert 'expose:\n      - "8000"' in text
    assert "MAVEDB_LINK_TRANSPORT: unified" in text
    assert "MAVEDB_LINK_MCP_PATH: /mcp" in text
    assert "MAVEDB_LINK_MIRROR__DATA_DIR: /home/app/reference/current" in text
    assert "MAVEDB_LINK_CACHE__DB_PATH: /home/app/cache/mavedb_cache.sqlite" in text
    assert "mavedb-reference:/home/app/reference:ro" in text
    assert "mavedb-cache:/home/app/cache" in text
    assert "bundle_url=latest" not in text
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
