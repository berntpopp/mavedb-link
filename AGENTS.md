# AGENTS.md

Shared instructions for AI coding agents working in this repository.

## What this project is

`mavedb-link` is a **read-only FastMCP 3.x server** that wraps the public MaveDB
REST API (`https://api.mavedb.org/api/v1`) for variant-effect / multiplexed-assay
data. It is a member of the GeneFoundry `*-link` fleet and federates into
`genefoundry-router` under the `mavedb` namespace. It is a *server* to MCP hosts
and a *client* to the MaveDB API.

- Primary code area: `mavedb_link/`
- Design spec: `docs/specs/2026-06-19-mavedb-link-design.md`
- Implementation plan: `docs/plans/2026-06-19-mavedb-link-implementation.md`

## Required check before handoff

```bash
make ci-local      # format-check, lint-ci, lint-loc (600-LOC budget), mypy strict, test-fast
```

Other targets: `make test`, `make test-integration` (live API), `make test-cov`
(coverage ≥80), `make lint`, `make typecheck`, `make dev`, `make smoke`,
`make docker-build`.

## Architecture — the two-plane boundary (non-negotiable)

- **Data plane** (`api/`, `services/`): returns **plain dicts**, raises **typed
  exceptions** (`mavedb_link.exceptions`). Never builds error envelopes.
- **MCP plane** (`mcp/`): domain-agnostic scaffolding. `run_mcp_tool` owns
  `success`/`_meta` and converts exceptions into **returned** (never raised)
  structured errors. Tool bodies attach `_meta.next_commands` and return
  `run_mcp_tool(name, call, context=...)`.

## Coding standards

- Python **3.12+**; deps + venv via **uv** (`uv sync --group dev`, `uv run`).
- Modern typing (`X | None`, builtin generics); `ruff` + `mypy strict` must pass.
- **600-LOC per module**, enforced by `scripts/check_file_size.py` (`make lint-loc`).
- TDD: write a failing test, see it fail, implement minimally, see it pass.
- FastMCP 3.x symbols are post-training-cutoff — **verify imports against the
  installed package** before relying on them.

## Project-specific guidance

- **Tool-Naming Standard v1**: tool names are unprefixed `verb_noun`, snake_case,
  ≤50 chars, canonical verbs (`get`/`search`/`resolve`/`list`/`find`). A CI test
  (`test_tool_names.py`) enforces this and the frozen `capabilities.TOOLS` list.
- **`capabilities.TOOLS` must equal the registered tool set** — update both when
  adding a tool.
- MaveDB reads are **public**: no auth, and the router never forwards caller
  tokens. Build expecting unauthenticated upstream calls.
- Keep upstream calls in `api/client.py` (retry/backoff/semaphore); services
  shape, tools wire `_meta`.

## Boundary

Research use only. Not clinical decision support. MaveDB functional scores are
experimental measurements, not clinical classifications. Mirror MaveDB's terms
and per-record licenses.
