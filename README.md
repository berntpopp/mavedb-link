# mavedb-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP 3.x](https://img.shields.io/badge/FastMCP-3.x-6E40C9)](https://github.com/jlowin/fastmcp)
[![Packaged with uv](https://img.shields.io/badge/packaged%20with-uv-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![Typed: mypy strict](https://img.shields.io/badge/typed-mypy%20strict-2A6DB2)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A read-only **FastMCP 3.x** server that grounds variant-effect work in
**[MaveDB](https://www.mavedb.org/)** — the database of Multiplexed Assays of
Variant Effect (deep mutational scanning and related functional assays that
assign quantitative functional scores to genetic variants). Part of the
GeneFoundry `*-link` fleet; federates into
[`genefoundry-router`](https://github.com/berntpopp/genefoundry-router) under the
`mavedb` namespace.

> ⚕️ **Research use only. Not clinical decision support.** MaveDB functional
> scores are experimental measurements, not clinical variant classifications.

## What it does

Wraps the public MaveDB REST API (`https://api.mavedb.org/api/v1`) as a small,
agent-ergonomic tool surface: **resolve/search → record → quantitative scores**,
cross-linked to genes, publications, and genome-mapped (GA4GH VRS) alleles.

| Tool | Purpose |
|------|---------|
| `search_score_sets` ⭐ | Full-text + faceted search of score sets (gene, organism, author, journal, keyword) |
| `get_score_set` | Full score-set record (targets, publications, license, dataset columns) |
| `get_variant_scores` | The quantitative variant × score table (CSV → parsed rows), paged |
| `get_gene_score_sets` | All published MAVE datasets for an HGNC gene symbol |
| `get_experiment` | Experiment record + child score-set URNs |
| `search_experiments` | Full-text experiment search |
| `get_mapped_variants` | Genome-mapped VRS alleles + ClinGen Allele IDs for a score set |
| `get_collection` | Curated collection members |
| `get_server_capabilities` | Discovery surface: tools, signatures, limits, error taxonomy |
| `get_diagnostics` | Live API reachability + version + runtime metrics |

⭐ = the router's pinned canonical entry point.

Resources: `mavedb://capabilities`, `mavedb://tools`, `mavedb://usage`,
`mavedb://reference`, `mavedb://research-use`, `mavedb://citation`.

## Quick start

```bash
# 1. Install (Python 3.12+, uv)
uv sync --group dev

# 2. Run the unified REST + MCP server over Streamable HTTP
uv run python server.py --transport unified --host 127.0.0.1 --port 8000

# 3. Verify liveness
curl -s localhost:8000/health | python -m json.tool

# 4. Add to an MCP host
claude mcp add --transport http mavedb-link http://127.0.0.1:8000/mcp
```

For Claude Desktop (stdio): `uv run python mcp_server.py`.

## Data model

```
ExperimentSet (urn:mavedb:00000001)
  └─ Experiment      (urn:mavedb:00000001-a)
       └─ ScoreSet   (urn:mavedb:00000001-a-1)   ── TargetGene(s) ── Taxonomy
            └─ Variant (urn:mavedb:00000001-a-1#1) ── MappedVariant (VRS allele)
```

Variants carry HGVS (`hgvs_nt`/`hgvs_pro`/`hgvs_splice`) and a quantitative
`score`; mapped variants project to a reference genome as GA4GH VRS alleles with
optional ClinGen Allele IDs. Scores download as CSV. Dataset licenses are per
score set (CC0 1.0 / CC BY 4.0 / CC BY-SA 4.0).

## Conventions

Mirrors the canonical `*-link` template: a thin data plane (`api/` httpx client +
`services/` returning plain dicts and raising typed exceptions) and a
domain-agnostic MCP plane (`mcp/` owning `success`/`_meta` and structured
errors). Every tool takes `response_mode` (`minimal|compact|standard|full`,
default `compact`) and chains via `_meta.next_commands`. Tool names follow
Tool-Naming Standard v1 (unprefixed `verb_noun`, ≤50 chars, canonical verbs).

## Develop & test

```bash
make ci-local          # format-check, lint, 600-LOC budget, mypy strict, unit tests
make test              # unit tests only
make test-integration  # live MaveDB API smoke (network)
make dev               # serve unified REST + MCP locally
```

## Docs

- Design spec — [`docs/specs/2026-06-19-mavedb-link-design.md`](docs/specs/2026-06-19-mavedb-link-design.md)
- Implementation plan — [`docs/plans/2026-06-19-mavedb-link-implementation.md`](docs/plans/2026-06-19-mavedb-link-implementation.md)
- Contributor guide — [`AGENTS.md`](AGENTS.md)

## License

[MIT](LICENSE) © Bernt Popp. MaveDB platform code is AGPL-3.0; each MaveDB
dataset carries its own license (CC0 / CC BY / CC BY-SA) — honor `license.shortName`.
