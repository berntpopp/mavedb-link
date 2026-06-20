"""Regenerate tests/eval/baseline.json by running the eval workflows.

Runs the deterministic eval harness (mocked facade, full stack) and writes the
per-workflow metric vector the CI regression gate compares against. Run after an
intentional change to the tool surface or fixtures:

    uv run python -m scripts.eval_baseline          # write baseline.json + print
    uv run python -m scripts.eval_baseline --check  # print only (no write)
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

import respx

from mavedb_link.api.client import MaveDBClient
from mavedb_link.config import MaveDBApiConfig
from mavedb_link.mcp.facade import create_mavedb_mcp
from mavedb_link.mcp.service_adapters import set_mavedb_service
from mavedb_link.services.mavedb_service import MaveDBService
from tests import fixtures
from tests.eval.harness import Recorder, mock_surface, read_structured, report
from tests.eval.workflows import ALL_WORKFLOWS

_BASELINE = pathlib.Path(__file__).resolve().parent.parent / "tests" / "eval" / "baseline.json"


async def _run() -> dict[str, dict[str, int]]:
    config = MaveDBApiConfig(base_url=fixtures.BASE_URL, cache_ttl=0, cache_size=0, max_retries=0)
    client = MaveDBClient(config)
    service = MaveDBService(client)
    set_mavedb_service(service)
    metrics: dict[str, dict[str, int]] = {}
    try:
        with respx.mock(base_url=fixtures.BASE_URL, assert_all_called=False) as router:
            mock_surface(router)
            for name, workflow in ALL_WORKFLOWS.items():
                rec = Recorder(create_mavedb_mcp(), read_structured)
                await workflow(rec)
                m = rec.metrics()
                # The baseline locks resource budgets; accuracy must be perfect.
                metrics[name] = {
                    "tool_calls": m["tool_calls"],
                    "tokens": m["tokens"],
                    "errors": m["errors"],
                }
    finally:
        set_mavedb_service(None)
        await client.aclose()
    return metrics


def main() -> None:
    """Run the workflows; write baseline.json unless --check is passed."""
    metrics = asyncio.run(_run())
    print(report(metrics))
    if "--check" not in sys.argv:
        _BASELINE.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {_BASELINE}")


if __name__ == "__main__":
    main()
