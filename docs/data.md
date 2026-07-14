# Data: the local mirror

`mavedb-link` is **mirror-primary, live-backup**. A local SQLite mirror built from the CC0
MaveDB Zenodo bulk dump serves reads first; any mirror-miss — for example a record newer than
the snapshot — transparently falls back to the live REST API
(`https://api.mavedb.org/api/v1`).

**Without a mirror the server still works.** It runs pure-live with no setup and no
regression in output shape. The mirror only changes *latency and provenance*, never the
response *shape*: mirror-served and live-served payloads are interchangeable (asserted in
`tests/unit/test_hybrid.py`).

## Source

| Fact | Value |
|------|-------|
| Bulk dump | [MaveDB Zenodo bulk dump](https://doi.org/10.5281/zenodo.11201736) |
| Concept DOI | `10.5281/zenodo.11201736` — always resolves to the newest version |
| Archive format | `.zip` through v4; `.tar.gz` from the 2026-06-24 release on (both supported) |
| Contents | `main.json` (camelCase records, incl. `scoreCalibrations`) + per-set `csv/<urn-dashed>.{scores,counts}.csv` |
| Licence | CC0 for the dump; **individual datasets carry their own licence** (see below) |
| Upstream producer | `mavedb.scripts.export_public_data` |

The mirror schema lives in `mavedb_link/data/schema.sql`; `MIRROR_SCHEMA_VERSION` in
`mavedb_link/constants.py` is bumped on any shape change.

## Building and refreshing

```bash
make data-build     # download the latest Zenodo dump + build data/mavedb.sqlite
make data-refresh   # rebuild only if Zenodo has a newer dump version
make data-status    # show snapshot date, Zenodo record, counts
make data-pack      # compress the mirror into a publishable artifact (+ sha256)
```

These wrap the `mavedb-link-data` CLI, which also exposes the subcommands the Make targets do
not:

| Subcommand | Purpose |
|------------|---------|
| `bootstrap` | Container entrypoint contract: reuse an existing DB → pull a prebuilt artifact → build locally. **Exits 0 even on total failure** so the server still starts live-only. |
| `build` | Download the latest Zenodo dump (or use a local `--dump`) and build the mirror. |
| `refresh` | Rebuild only when Zenodo has a newer version than the local DB. |
| `status` | Print the local mirror's provenance. |
| `pull` / `pack` / `publish` | Prebuilt-artifact transport via GitHub Releases. |

The builder streams the dump into SQLite atomically (`os.replace`), one CSV member at a time,
so peak memory is roughly one CSV. Dump CSV headers are denamespaced back to the live shape
(`scores.score` → `score`, preserving dotted columns like `exp.score`). Per-set score
distributions are precomputed at build time.

Prebuilt `mavedb.sqlite.zst` artifacts are published to GitHub Releases by
`.github/workflows/data.yml` (monthly, plus manual dispatch).

## What is served from the mirror

Score-set and experiment records, the scores/counts tables, full-text search, the score
distribution, and the `get_gene_score_sets` score-set listing are served from the local index.

**The annotations gap.** The Zenodo bulk dump omits `csv/*.annotations.csv`, so the
GA4GH VRS / ClinGen Allele ID mapped-variant layer is **not** in the mirror. It is backfilled
**lazily from the live API, per score set**, into an on-disk cache: the first tool call that
touches a score set fetches its mapped variants and writes them through, and repeat
`get_mapped_variants`, `find_variant(variant_urn=)`, and target-relative
`find_variant(hgvs=, gene_symbol=)` reads then serve from the cache. The mirror schema still
accepts annotations CSVs if a future export restores them.

Some reads stay live by design:

- Rich gene identity is fetched live but memoised and time-boxed, degrading to a thin
  mirror-derived identity (symbol + organism) on timeout.
- HGVS validation is memoised.
- The calibration-by-class listing stays live.

```bash
mavedb-link-cache status      # inspect the lazy mapped-variant cache
mavedb-link-cache clear --yes # drop it
```

## Provenance on every response

Each response stamps `_meta.data_source` as `mirror` | `live` | `mixed`, alongside
`mirror_as_of` (the snapshot date). `get_diagnostics.mirror` reports snapshot status and
`get_diagnostics.cache` reports the mapped-variant cache state.

Local mirror and cache files are **operational state only**. They are written to disk to make
public reads fast and offline; they do not change upstream or domain data. The MCP/API surface
is read-only with respect to MaveDB: it never mutates upstream records and does not accept
caller credentials.

## Licensing and citation

MaveDB has **no blanket data licence**. Dataset licences are **per score set** — CC0 1.0,
CC BY 4.0, or CC BY-SA 4.0 — and each record's `license.shortName` must be honoured. The
MaveDB *platform* code is AGPL-3.0; this repository is MIT.

Cite the platform, and alongside it the specific score-set URN, its licence, and its primary
publication:

> Esposito D, Weile J, Shendure J, et al. MaveDB: an open-source platform to distribute and
> interpret data from multiplexed assays of variant effect. *Genome Biology*. 2019;20(1):223.
> doi:10.1186/s13059-019-1845-6

## Disabling the mirror

```bash
MAVEDB_LINK_MIRROR__ENABLED=false   # serve live-only
```

Note the double underscore — it is the nested-settings delimiter, not a typo. See
[configuration.md](configuration.md) for the rest of the mirror and cache knobs.
