# mavedb-link — Implementation Plan

**Date:** 2026-06-19 · **Spec:** `docs/specs/2026-06-19-mavedb-link-design.md`

> Historical record — this document records the implementation plan as of its date. Current
> behavior is defined by implemented code, standards, release evidence, and tests.

Build order (foundation → data plane → MCP plane → tests → integration → router).
Definition of done = `make ci-local` green + coverage ≥80% + live smoke passes.

## Task 1 — Project scaffold & tooling
`pyproject.toml` (hatchling, deps: fastapi/uvicorn/pydantic/pydantic-settings/
httpx/structlog/orjson/rich/typer/mcp/fastmcp; dev: pytest stack + respx + ruff +
mypy), `Makefile`, `scripts/check_file_size.py` (600 LOC), `.loc-allowlist`,
`.pre-commit-config.yaml`, `.gitignore`, `.python-version`, `.env.example`,
`LICENSE` (MIT), `README.md`, `AGENTS.md`, `CLAUDE.md`, `docker/`.

## Task 2 — Import smoke
`uv sync --group dev`; verify FastMCP 3.x symbols against the installed package
(`FastMCP`, `@mcp.tool`, `ToolAnnotations`, `http_app`, middleware base).

## Task 3 — Foundation modules
`__init__` (`__version__`), `constants.py` (base URL, citation, license, limits,
error codes), `exceptions.py` (MaveDB error hierarchy), `identifiers.py` (URN
regex parse/classify/validate), `config.py` (pydantic-settings, `MAVEDB_LINK_`
prefix), `logging_config.py`, `buildinfo.py`.

## Task 4 — API client (data plane)
`api/client.py` — shared `httpx.AsyncClient`, semaphore, jittered retry on
429/5xx/timeout/network, status→exception mapping, JSON + CSV helpers.
Tests: `test_client.py` (respx — success, 404, 422, 429-retry, 5xx, CSV).

## Task 5 — Services (data plane)
`services/shaping.py` (per-entity `response_mode` projection), `services/scores.py`
(CSV→rows + paging/caps), `services/mavedb_service.py` (async methods returning
plain dicts + pagination blocks). Tests: `test_shaping.py`, `test_scores.py`,
`test_service.py` (respx-backed).

## Task 6 — MCP scaffolding (copy mondo, swap nouns)
`mcp/annotations.py`, `mcp/metrics.py`, `mcp/arg_help.py` (ARG_ALIASES for
mavedb), `mcp/envelope.py` (taxonomy, classify, `_shape_meta`, arg-error
builder), `mcp/schemas.py` (permissive output schemas per tool), `mcp/middleware.py`,
`mcp/service_adapters.py` (async singleton service + set/reset), `mcp/resources.py`
(instructions + notices), `mcp/next_commands.py` (cmd/page/widen + after_* chains),
`mcp/capabilities.py` (build_capabilities + content hash + resources).

## Task 7 — Tools (MCP plane)
`mcp/tools/_common.py` (annotated arg types), `discovery.py`, `score_sets.py`,
`variants.py`, `genes.py`, `experiments.py`, `collections.py`, `__init__.py`
(register fan-out). `mcp/facade.py` (`create_mavedb_mcp`).

## Task 8 — Entry points
`app.py` (FastAPI: `/health` + `/`, client-close lifespan), `server_manager.py`
(unified/http/stdio), `server.py`, `mcp_server.py`. `[project.scripts]`.

## Task 9 — MCP surface tests
`test_tool_names.py`, `test_output_schemas.py`, `test_tools_e2e.py`,
`test_next_commands.py`, `test_capabilities.py`, `test_metrics.py`,
`test_config.py`, `test_identifiers.py`. `tests/conftest.py` (fake service +
respx-backed real service fixtures; canned JSON/CSV fixtures).

## Task 10 — Verify
`make ci-local`; fix to green. Run live integration smoke (`-m integration`).
Dispatch parallel review (Tool-Naming compliance, adversarial bug hunt, live API
contract check). Add the `servers.yaml` line + `.env` note to the router.

## Parallelization
Research already fanned out (router contract, template, API map). For the build,
the core is written coherently in-session; the **final verification fans out**
(parallel review + live smoke + router-registration agents).
