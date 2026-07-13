"""Deployment config contract for the NPM-hosted GeneFoundry fleet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_config_declares_the_init_sidecar_role() -> None:
    """The central compose gate authorizes the sidecar by role, never by name."""
    config = json.loads((ROOT / "container-release.json").read_text())
    (auxiliary,) = config["service"]["auxiliary"]
    assert auxiliary["name"] == "mavedb-data-init"
    assert auxiliary["role"] == "init"
    # The bundle is fetched from GitHub Releases, so the sidecar needs egress.
    assert auxiliary["egress"] == "approved-networks"
    assert sorted(auxiliary["writable_targets"]) == ["/data", "/tmp"]  # noqa: S108
    assert config["smoke"]["profile"] == "immutable-bundle"


def test_runtime_cache_is_declared_outside_the_immutable_reference_root() -> None:
    """A cache write must never be able to mutate the verified mirror."""
    config = json.loads((ROOT / "container-release.json").read_text())
    cache = Path(config["runtime_cache"]["path"])
    assert not cache.is_relative_to(Path("/data/reference"))
    assert cache.is_relative_to(Path("/data"))


def test_gated_compose_declares_no_top_level_extension_fields() -> None:
    """`docker compose config` emits `x-*` verbatim and the central policy rejects it."""
    config = json.loads((ROOT / "container-release.json").read_text())
    for name in config["service"]["compose_files"]:
        text = (ROOT / name).read_text()
        assert not any(line.startswith("x-") for line in text.splitlines())


def test_production_compose_pulls_the_pinned_bundle() -> None:
    """`bootstrap` reuses whatever is on the volume; production must install the pin."""
    production = (ROOT / "docker/docker-compose.prod.yml").read_text()
    assert '["mavedb-link-data", "pull"]' in production
    assert "MAVEDB_LINK_MIRROR__BUNDLE_RELEASE_TAG" in production
    assert "MAVEDB_LINK_MIRROR__BUNDLE_EXPECTED_EXPANDED_SHA256" in production


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
    # /data and /tmp are the only writable mount targets the fleet compose policy
    # approves, so the mirror and the deletable cache share one volume at /data.
    assert "MAVEDB_LINK_MIRROR__DATA_DIR: /data/reference/current" in text
    assert "MAVEDB_LINK_CACHE__DB_PATH: /data/cache/mavedb_cache.sqlite" in text
    assert "mavedb-data:/data" in text
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
