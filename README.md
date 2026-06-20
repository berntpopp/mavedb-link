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
agent-ergonomic tool surface: **resolve/search → record → quantitative scores →
calibrated interpretation**, cross-linked to genes, publications, and
genome-mapped (GA4GH VRS) alleles. Crucially, it surfaces MaveDB's *curated
interpretation layer* — functional-classification thresholds with ACMG PS3/BS3
evidence strength and OddsPath ratios — so a score is never returned as a bare,
uninterpretable float.

| Tool | Purpose |
|------|---------|
| `search_score_sets` ⭐ | Full-text + faceted search of score sets (gene, organism, author, journal, keyword) |
| `get_score_set` | Score-set record (targets, publications, license) **+ `score_calibrations`: ACMG/OddsPath thresholds** |
| `get_variant_scores` | The quantitative variant × score table (paged); **each row carries its calibrated functional class** |
| `get_variant_score` | ONE variant's score **+ per-calibration classification** (by variant URN, or score-set URN + hgvs) |
| `get_classified_variants` | Every variant in a calibrated functional class (e.g. all `abnormal`/PS3), with scores |
| `get_score_distribution` | Server-side summary stats (quartiles, histogram) + a query score's percentile + class |
| `find_variant` | One variant's score + class across **every** score set (cross-dataset) — anchor by GA4GH VRS id, `variant_urn`, or a bare `hgvs=` (+ optional `gene=`) resolved to VRS internally |
| `get_hgvs_validation` | Validate an HGVS string and surface *why* it's invalid (reference mismatch, missing accession) |
| `get_gene_score_sets` | All published MAVE datasets for an HGNC gene symbol (lean listing: `has_calibrations` flag, not the inlined ladder — open `get_score_set` for thresholds) |
| `get_experiment` | Experiment record + child score-set URNs |
| `search_experiments` | Full-text experiment search |
| `get_mapped_variants` | Genome-mapped VRS alleles + ClinGen Allele IDs for a score set |
| `get_collection` | Curated collection members (URN from a score set's `official_collections`, or mavedb.org) |
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

## Local mirror (primary) + live API (backup)

To make lookups fast and offline, `mavedb-link` can serve from a **local SQLite
mirror** built from the CC0 [MaveDB Zenodo bulk dump](https://zenodo.org/records/15653325)
(concept DOI `10.5281/zenodo.11201736`), falling back to the live REST API on any
mirror-miss (e.g. a record newer than the snapshot). Without a mirror it runs
pure-live — no setup required.

```bash
make data-build     # download the latest Zenodo dump + build data/mavedb.sqlite
make data-status    # show snapshot date, Zenodo record, counts
make data-refresh   # rebuild only if Zenodo has a newer dump version
```

Score-set/experiment records, the scores/counts tables, full-text search, the
score distribution, the cross-dataset VRS rollup, the per-set mapped-variant
enumeration (current-only, compact/minimal), HGVS→VRS resolution for
`find_variant(hgvs=)`, and the `get_gene_score_sets` score-set listing are served
from the local index; rich gene identity is fetched live but memoised + time-boxed
(degrading to a thin mirror identity), and the richer mapped-variant reads (full
VRS objects), HGVS validation (memoised), and the calibration-by-class listing stay
live. Each response's `_meta.data_source` reports
`mirror` | `live` | `mixed`, with `mirror_as_of` (the snapshot date);
`get_diagnostics.mirror` reports live status.
In Docker the entrypoint runs `mavedb-link-data bootstrap` (reuse → pull prebuilt
artifact → build, else live-only) and persists the mirror on a volume; prebuilt
`mavedb.sqlite.zst` artifacts are published to GitHub Releases by
`.github/workflows/data.yml`. Disable with `MAVEDB_LINK_MIRROR__ENABLED=false`.

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
