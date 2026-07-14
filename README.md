# mavedb-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/mavedb-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/mavedb-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/mavedb-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/mavedb-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A read-only **Model Context Protocol** (MCP) server, built on FastMCP 3.x, that grounds
variant-effect work in [MaveDB](https://www.mavedb.org/) — the database of Multiplexed Assays
of Variant Effect, which assigns quantitative functional scores to variants by deep mutational
scanning and related assays. Part of the GeneFoundry `*-link` fleet; federates into
[`genefoundry-router`](https://github.com/berntpopp/genefoundry-router) under the `mavedb`
namespace.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

MaveDB's REST API hands back a score and its interpretation as **separate, unjoined parts of
the record**. The scores arrive as a CSV column of bare floats; whether a given float means
*abnormal* depends on that score set's own curated calibration ladder, which lives elsewhere in
the record and which the caller must fetch and apply by hand. A raw MAVE score is
uninterpretable on its own, and an agent handed one will either guess or hallucinate a
threshold.

This server never returns a naked score. It carries MaveDB's *curated interpretation layer* —
functional-classification thresholds with ACMG PS3/BS3 evidence strength and OddsPath ratios —
alongside every value, and resolves identifiers (HGVS, GA4GH VRS, URN) internally instead of
forcing a map-first round-trip. `find_variant` answers the question the record-oriented API
leaves you to assemble yourself: *what does every MAVE dataset say about this one variant?*

Reads are served from a local SQLite mirror of the CC0 Zenodo bulk dump and fall back to the
live API on a miss, so lookups are fast and work offline.

## Quick start

Hosted — no install required:

```bash
claude mcp add --transport http mavedb-link https://mavedb-link.genefoundry.org/mcp
```

Or run it locally (Python 3.12+, [uv](https://github.com/astral-sh/uv)):

```bash
uv sync --group dev
uv run python server.py --transport unified --host 127.0.0.1 --port 8000
curl -s localhost:8000/health | python -m json.tool
claude mcp add --transport http mavedb-link http://127.0.0.1:8000/mcp
```

**No data build is required** — the server runs pure-live out of the box. Building the local
mirror is optional, and makes lookups fast and offline:

```bash
make data-build     # download the latest Zenodo dump → data/mavedb.sqlite
```

`--transport unified` serves REST **and** MCP at `/mcp`; `--transport http` is REST/health only
and exposes **no MCP endpoint** — router and MCP clients need `unified`. For Claude Desktop
(stdio), run `uv run python mcp_server.py` instead.

## Tools

| Tool | Purpose |
|------|---------|
| `search_score_sets` | Full-text + faceted search of score sets (gene, organism, author, journal, keyword) |
| `get_score_set` | Score-set record (targets, publications, licence) **+ `score_calibrations`: ACMG/OddsPath thresholds** |
| `get_variant_scores` | The quantitative variant × score table (paged); each row carries its calibrated functional class |
| `get_variant_score` | One variant's score + per-calibration classification (by variant URN, or score-set URN + HGVS) |
| `get_classified_variants` | Every variant in a calibrated functional class (e.g. all `abnormal` / PS3), with scores |
| `get_score_distribution` | Server-side summary stats (quartiles, histogram) + a query score's percentile and class |
| `find_variant` | One variant's score + class across **every** score set — anchored by GA4GH VRS id, `variant_urn`, or a bare `hgvs=` (+ optional `gene_symbol=`) resolved to VRS internally |
| `get_hgvs_validation` | Validate an HGVS string and surface *why* it is invalid (reference mismatch, missing accession) |
| `get_gene_score_sets` | All published MAVE datasets for an HGNC gene symbol (lean listing with a `has_calibrations` flag) |
| `get_experiment` | Experiment record + child score-set URNs |
| `search_experiments` | Full-text experiment search |
| `get_mapped_variants` | Genome-mapped GA4GH VRS alleles + ClinGen Allele IDs for a score set |
| `get_collection` | Curated collection members (URN from a score set's `official_collections`, or mavedb.org) |
| `get_server_capabilities` | Discovery surface: tools, signatures, limits, error taxonomy |
| `get_diagnostics` | Live API reachability + version + mirror/cache/runtime metrics |

`search_score_sets` is the router's pinned entry point. Leaf names are unprefixed per
[Tool-Naming Standard v1](https://github.com/berntpopp/genefoundry-router/blob/main/docs/TOOL-NAMING-STANDARD-v1.md);
behind `genefoundry-router` they surface as `mavedb_<tool>` — e.g. `mavedb_search_score_sets`.

The server also registers the `mavedb://` resource family (capabilities, tools, usage,
reference, research-use, citation) and a `response_mode` token-cost lever on every tool; see
[docs/architecture.md](docs/architecture.md).

## Data & provenance

**Upstream** is the public [MaveDB REST API](https://api.mavedb.org/api/v1)
(`https://api.mavedb.org/api/v1`) — read-only, no credentials, no API key. The router never
forwards a caller's token upstream.

**Refresh model: mirror-primary, live-backup.** A local SQLite mirror is built from the CC0
[MaveDB Zenodo bulk dump](https://doi.org/10.5281/zenodo.11201736) (concept DOI
`10.5281/zenodo.11201736`, always resolving to the newest version); a mirror-miss — for example
a record newer than the snapshot — falls back transparently to the live API. Each response
stamps `_meta.data_source` (`mirror` | `live` | `mixed`) and `mirror_as_of`. Full mechanics,
including the lazily-backfilled VRS/ClinGen layer: [docs/data.md](docs/data.md).

**Data licence: per score set.** MaveDB has no blanket data licence — each dataset carries its
own (CC0 1.0, CC BY 4.0, or CC BY-SA 4.0). Honour each record's `license.shortName`.

**Citation.** Cite the platform, and alongside it the specific score-set URN, its licence, and
its primary publication:

> Esposito D, Weile J, Shendure J, et al. MaveDB: an open-source platform to distribute and
> interpret data from multiplexed assays of variant effect. *Genome Biology*. 2019;20(1):223.
> [doi:10.1186/s13059-019-1845-6](https://doi.org/10.1186/s13059-019-1845-6)

MaveDB functional scores are **experimental measurements, not clinical variant
classifications**; a calibrated class such as `abnormal`/PS3 is assay evidence, not a
pathogenicity call.

## Documentation

- [Architecture](docs/architecture.md) — the MaveDB data model, the calibration layer, the response contract, and the `mavedb://` resources.
- [Configuration](docs/configuration.md) — every `MAVEDB_LINK_*` variable, the transport modes, and the Host/Origin request guard.
- [Data & the local mirror](docs/data.md) — the Zenodo dump, build/refresh commands, and the lazy mapped-variant cache.
- [Deployment](docs/deployment.md) — the container stacks, the data-init container, bundle pinning, and the reverse proxy.
- [Design spec](docs/specs/2026-06-19-mavedb-link-design.md) · [implementation plan](docs/plans/2026-06-19-mavedb-link-implementation.md).
- [`AGENTS.md`](AGENTS.md) — engineering conventions: the two-plane boundary, tool naming, and the mirror invariant.

## Contributing

See [`AGENTS.md`](AGENTS.md) for engineering conventions and the repository layout.
`make ci-local` is the definition-of-done gate: format, lint, the line budget, the README
standard, mypy strict, and the tests.

## License

[MIT](LICENSE) © Bernt Popp — this repository's code. Note the three layers: the MaveDB
*platform* code is AGPL-3.0, and each MaveDB *dataset* carries its own licence (CC0 1.0 /
CC BY 4.0 / CC BY-SA 4.0) — honour `license.shortName` on every record you use.
