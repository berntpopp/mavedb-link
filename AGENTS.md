# AGENTS.md

Shared instructions for AI coding agents working in this repository.

## What this project is

`mavedb-link` is a **read-only FastMCP 3.x server** for variant-effect /
multiplexed-assay data from MaveDB. It is a member of the GeneFoundry `*-link`
fleet and federates into `genefoundry-router` under the `mavedb` namespace. It is
a *server* to MCP hosts and a *client* to the MaveDB API.

It is **mirror-primary, live-backup**: a local SQLite mirror built from the CC0
MaveDB Zenodo bulk dump (concept DOI `10.5281/zenodo.11201736`) serves reads
first; a mirror-miss (e.g. a record newer than the snapshot) transparently falls
back to the live REST API (`https://api.mavedb.org/api/v1`). When no mirror is
built, the server runs pure-live (no regression). See **Local mirror** below.

- Primary code area: `mavedb_link/`
- Mirror code: `mavedb_link/ingest/` (acquire+build), `mavedb_link/data/`
  (schema, repository, hybrid client), CLI `mavedb-link-data`
- Design spec: `docs/specs/2026-06-19-mavedb-link-design.md`
- Implementation plan: `docs/plans/2026-06-19-mavedb-link-implementation.md`

## Required check before handoff

```bash
make ci-local      # format-check, lint-ci, lint-loc (600-LOC budget), mypy strict, test-fast
```

Other targets: `make test`, `make test-integration` (live API), `make test-cov`
(coverage ≥80), `make lint`, `make typecheck`, `make dev`, `make smoke`,
`make docker-build`. `make eval` runs the deterministic multi-call eval workflows
+ the token/error-rate regression gate (also run inside `ci-local`); regenerate
its baseline with `make eval-baseline` after an intentional surface change.

## Architecture — the two-plane boundary (non-negotiable)

- **Data plane** (`api/`, `services/`): returns **plain dicts**, raises **typed
  exceptions** (`mavedb_link.exceptions`). Never builds error envelopes.
- **MCP plane** (`mcp/`): domain-agnostic scaffolding. `run_mcp_tool` owns
  `success`/`_meta` and converts exceptions into **returned** (never raised)
  structured errors. Tool bodies attach `_meta.next_commands` and return
  `run_mcp_tool(name, call, context=...)`.

## Local mirror (mirror-primary, live-backup)

- **Source**: the CC0 Zenodo bulk dump — `main.json` (camelCase records, incl.
  `scoreCalibrations`) + per-set `csv/<urn-dashed>.{scores,counts}.csv`. The
  schema still accepts `annotations` CSVs if a future export restores them, but
  the verified Zenodo v4 zip member listing omits `csv/*.annotations.csv`.
  Built by `mavedb.scripts.export_public_data` upstream.
- **Build** (`ingest/builder.py`): streams the dump zip into SQLite atomically
  (`os.replace`), one CSV member at a time (peak memory ≈ one CSV). Dump CSV
  headers are denamespaced back to the live shape (`scores.score` → `score`,
  preserving dotted columns like `exp.score`). Per-set distributions are
  precomputed; when annotations CSVs are present, their VRS digest + ClinGen
  layer is indexed for cross-dataset `find_variant`. Current v4 mirrors have an
  empty annotations layer and rely on lazy live backfill into the mapped-variant
  cache. Schema in `data/schema.sql`; bump `MIRROR_SCHEMA_VERSION` (in
  `constants.py`) on any shape change.
- **Serve** (`data/hybrid.py`): `HybridClient` subclasses `MaveDBClient` and
  overrides `get_json`/`get_text`/`post_json` to answer from the mirror, else
  `super()` (live). It also exposes duck-typed helpers the services prefer when
  present: `vrs_for_hgvs` (HGVS→VRS via the `hgvs_index`, for `find_variant(hgvs=)`)
  and `gene_identity` (thin symbol+organism fallback for `get_gene_score_sets`,
  whose score-set listing is mirror-served while rich `/genes` identity is
  memoised + time-boxed in `services/resolvers`). The whole
  service/shaping/calibration stack consumes it unchanged. `ensure_mapped_variants`
  lazily fetches per-set mapped variants from live, writes them through to the
  on-disk cache, and makes repeat VRS/ClinGen reads mirror-fast while preserving
  live authority/provenance. `_meta.data_source` (`mirror`|`live`|`mixed`) +
  `mirror_as_of` report provenance per call; `get_diagnostics.mirror` reports
  snapshot status and `get_diagnostics.cache` reports mapped-cache state.
- **Acquire/refresh** (`mavedb-link-data` CLI): `bootstrap` (reuse → pull
  prebuilt artifact → build, else degrade to live-only), `build`, `refresh`,
  `status`, `pull`, `pack`, `publish`. Prebuilt `mavedb.sqlite.zst` artifacts are
  published to GitHub Releases by `.github/workflows/data.yml` (monthly + manual).
- **Mapped-cache ops** (`mavedb-link-cache` CLI): `status`, `clear --yes`.
- **Invariant**: the mirror only changes latency/provenance, never the output
  *shape* — mirror-served and live-served payloads must be interchangeable
  (verify both in `tests/unit/test_hybrid.py`).

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
- **Consolidation bias (do not proliferate tools).** The surface is ~15 tools —
  inside Google's 10–20 guidance, and progressive disclosure mitigates the count —
  but the bias going forward is *consolidation*: prefer extending an existing tool
  with a parameter (e.g. `find_variant` accepts a `variant_urn` and resolves its
  VRS internally) over adding a new thin tool, and resolve identifiers internally
  where MaveDB supports it rather than forcing a map-first round-trip on the caller.
- MaveDB reads are **public**: no auth, and the router never forwards caller
  tokens. Build expecting unauthenticated upstream calls.
- Keep upstream calls in `api/client.py` (retry/backoff/semaphore); services
  shape, tools wire `_meta`.

### Fleet deploy contract

- `docker/docker-compose.npm.yml` is the file the fleet controller
  (`strato_v6_docker_npm`) deploys and validates. Every service there —
  including the `mavedb_data_init` sidecar — declares `user: "<uid>:<gid>"`
  numerically: this image's own uid:gid from `docker/Dockerfile` (currently
  `999:999`), never copied from a sibling `-link` repo.
- `user` must **not** appear in the Compose files listed in
  `container-release.json` (`docker/docker-compose.yml`,
  `docker/docker-compose.prod.yml`) — the shared release gate
  (`container_release.py validate-compose`) forbids it there.
- The overlay inlines the image reference and shared environment per service
  rather than a top-level `x-image`/`x-data` YAML anchor: the controller's
  Compose projection (`canonical_projection`) rejects any rendered top-level
  key outside `{name, services, networks, volumes, configs, secrets}`, and
  `docker compose config --format json` echoes `x-*` keys back at the top
  level even when only referenced through an anchor.
- Both rules are enforced by `tests/unit/test_npm_deploy_config.py`
  (`test_npm_overlay_declares_numeric_user_for_every_service`,
  `test_release_compose_files_never_declare_user`).
- **Release checklist** (fleet controller pulls a tagged, attested image — it
  never builds from source): bump `pyproject.toml`, `uv lock`, add a
  `CHANGELOG.md` heading `## [x.y.z] - YYYY-MM-DD`, bump `CITATION.cff`
  `version:` **and** `date-released:` (the file's header says it is generated
  externally, but `test_version_single_source.py::
  test_citation_matches_current_changelog_release` enforces `date-released`
  to equal *this repo's* `CHANGELOG.md` heading date for the current version —
  follow the test, not the header comment), tag `vx.y.z`, then approve the
  `release` environment gate via
  `gh api repos/berntpopp/mavedb-link/actions/runs/<id>/pending_deployments`
  (it can gate twice; `status: waiting` is the gate, not a slow build).

## Boundary

Research use only. Not clinical decision support. MaveDB functional scores are
experimental measurements, not clinical classifications. Mirror MaveDB's terms
and per-record licenses.
