# mavedb-link — Design Spec

**Date:** 2026-06-19
**Status:** Approved for implementation (autonomous build per user directive)
**Author:** Senior MCP engineer (Claude)

## 1. Purpose

`mavedb-link` is a read-only **FastMCP 3.x** server that grounds variant–effect
work in **MaveDB** — the database of Multiplexed Assays of Variant Effect (deep
mutational scanning and related functional assays that assign quantitative
functional scores to genetic variants). It is the newest member of the
GeneFoundry `*-link` fleet and federates into `genefoundry-router` under the
`mavedb` namespace.

It exposes MaveDB's public REST API (`https://api.mavedb.org/api/v1`) as a small,
agent-ergonomic tool surface: resolve/search → record → quantitative scores,
with cross-references to genes, publications, and genome-mapped (VRS) alleles.

> ⚕️ **Research use only. Not clinical decision support.** MaveDB functional
> scores are experimental measurements, not clinical classifications.

## 2. Fleet conventions (non-negotiable)

Mirrors the canonical `*-link` template (gnomad-link data spine + mondo-link
`mcp/` scaffolding):

- Python 3.12+, **uv**, **ruff** (line-length 100), **mypy strict**.
- **≤600 LOC per module** (`scripts/check_file_size.py`, `make lint-loc`),
  `.loc-allowlist` for any grandfathered file (target: none).
- **Two-plane boundary**: the data plane (`api/`, `services/`) returns plain
  dicts and raises typed exceptions; the MCP plane (`mcp/`) owns `success`/`_meta`
  and converts exceptions into *returned* (never raised) structured errors via
  `run_mcp_tool`.
- **Tool-Naming Standard v1**: unprefixed `verb_noun`, snake_case, ≤50 chars,
  canonical verbs (`get`/`search`/`resolve`/`list`/`find`), fleet-canon arg names
  (`response_mode`, `limit`, `offset`, `gene_symbol`).
- **`response_mode`** verbosity tiers `minimal|compact|standard|full` (default
  `compact`); `_meta` tiered to match.
- **Capabilities + diagnostics** discovery tools; `capabilities_version` content
  hash echoed in every `_meta`; `mavedb://` resources.
- **Citation contract**: `recommended_citation` + per-record `license` surfaced;
  research-use notice on instructions, capabilities, and a resource.
- Streamable-HTTP transport at `/mcp` + `/health`; stdio entry for Claude Desktop.
  No auth (router never forwards caller tokens; MaveDB public reads need none).

## 3. Data model (MaveDB)

```
ExperimentSet (urn:mavedb:00000001)
  └─ Experiment      (urn:mavedb:00000001-a)
       └─ ScoreSet   (urn:mavedb:00000001-a-1)   ── TargetGene(s) ── Taxonomy
            └─ Variant (urn:mavedb:00000001-a-1#1) ── MappedVariant (VRS allele)
```

- **URNs**: experiment-set `urn:mavedb:\d{8}`, experiment `…-<letter>`, score-set
  `…-<letter>-<n>`, variant `<score_set>#<index>` (meta-analysis uses `-0-` block).
- **Variants** carry HGVS (`hgvs_nt`, `hgvs_pro`, `hgvs_splice`) and a quantitative
  `score`; **MappedVariant** projects to a reference genome as a GA4GH VRS allele
  with an optional ClinGen Allele ID.
- **Scores** download as `text/csv` from `/score-sets/{urn}/scores`.
- **Licenses** are per score set: CC0 1.0, CC BY 4.0, CC BY-SA 4.0.

## 4. Tool surface (v1 — 10 read-only tools)

| Tool | Verb | Endpoint(s) | Purpose |
|------|------|-------------|---------|
| `get_server_capabilities` | get | (static) | Discovery surface, signatures, limits, error taxonomy |
| `get_diagnostics` | get | `GET /api/version` | Live API reachability + version + runtime metrics |
| `search_score_sets` ⭐ | search | `POST /score-sets/search` | Faceted/full-text score-set search (**entrypoint**) |
| `get_score_set` | get | `GET /score-sets/{urn}` | Full score-set record (targets, publications, license) |
| `get_variant_scores` | get | `GET /score-sets/{urn}/scores` | The quantitative variant×score table (CSV → rows), paged |
| `get_gene_score_sets` | get | `GET /genes/{symbol}` | All published MAVE datasets for an HGNC gene symbol |
| `get_experiment` | get | `GET /experiments/{urn}` | Experiment record + child score-set URNs |
| `search_experiments` | search | `POST /experiments/search` | Full-text experiment search |
| `get_mapped_variants` | get | `GET /score-sets/{urn}/mapped-variants` | Genome-mapped VRS alleles + ClinGen Allele IDs |
| `get_collection` | get | `GET /collections/{urn}` | Curated collection members |

⭐ = pinned router `entrypoint`.

Resources: `mavedb://capabilities`, `mavedb://tools`, `mavedb://usage`,
`mavedb://reference`, `mavedb://research-use`, `mavedb://citation`.

## 5. Architecture / modules

```
mavedb_link/
  __init__.py config.py constants.py identifiers.py exceptions.py
  logging_config.py buildinfo.py app.py server_manager.py
  api/        client.py                 # httpx async client: retry/backoff/semaphore
  services/   mavedb_service.py shaping.py scores.py
  mcp/        facade.py envelope.py capabilities.py annotations.py schemas.py
              next_commands.py resources.py arg_help.py middleware.py metrics.py
              service_adapters.py
  mcp/tools/  __init__.py _common.py discovery.py score_sets.py variants.py
              genes.py experiments.py collections.py
server.py  mcp_server.py  scripts/check_file_size.py  docker/  tests/
```

- **`api/client.py`** — one shared `httpx.AsyncClient`, opened lazily; an
  `asyncio.Semaphore` bounds in-flight requests; jittered exponential backoff
  retries 429/5xx/timeouts/network; maps `404→NotFound`, `400/422→InvalidInput`,
  `429→RateLimit`, `5xx/network→ServiceUnavailable`. JSON + CSV helpers.
- **`services/mavedb_service.py`** — async domain methods returning plain dicts;
  validates URNs/symbols; assembles pagination blocks; delegates field trimming.
- **`services/shaping.py`** — `response_mode` projection per entity (score set,
  experiment, target gene, gene result, mapped variant, collection).
- **`services/scores.py`** — parse the scores CSV into typed rows; page/cap.
- **`mcp/*`** — copied from mondo-link with MaveDB nouns; the envelope/metrics/
  middleware/arg_help are domain-agnostic.

## 6. Error handling

Fleet taxonomy: `invalid_input`, `not_found`, `ambiguous_query`,
`data_unavailable`, `rate_limited`, `upstream_unavailable`, `internal_error`.
Errors are **returned** as `{success:false, error_code, message, retryable,
recovery_action, _meta:{next_commands}}`, never raised to the client.

## 7. Testing

- **Unit** (network-free): client (respx-mocked httpx), service, shaping, scores
  CSV parsing, identifiers, capabilities (content-hash stability, taxonomy
  completeness), metrics, config.
- **MCP surface** (fake injected service): `test_tool_names` (Tool-Naming v1
  regex + canonical verbs + no self-prefix + frozen `TOOLS` list),
  `test_output_schemas` (every tool × every `response_mode` × error validates
  against its `output_schema`), `test_tools_e2e` (facade happy/error paths,
  `_meta` tiering, `next_commands` chaining, alias rewrite).
- **Integration** (`-m integration`, excluded from default CI): live MaveDB API
  smoke over the real endpoints.
- Coverage `fail_under = 80`. Gate = `make ci-local`
  (`format-check lint-ci lint-loc typecheck test-fast`).

## 8. Router integration

Add to `genefoundry-router/servers.yaml`:

```yaml
- { name: mavedb, repo: berntpopp/mavedb-link, url_env: GF_MAVEDB_URL, namespace: mavedb,
    tags: [variant, mave, functional-assay, variant-effect, score-set],
    entrypoints: [search_score_sets] }
```

`GF_MAVEDB_URL=https://mavedb-link.genefoundry.org/mcp`. Deploys via DNS +
container + NPM. An unset URL leaves the backend dormant (router still starts).

## 9. Non-goals (v1)

No writes/auth, no private/unpublished records, no score-calibration or VA-Spec
statement construction, no full Pydantic mirroring of 205 upstream schemas, no
bulk global dump. These can follow in later milestones.
