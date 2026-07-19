# Lazy Mapped-Variant Cache — Design Spec

**Status:** Approved (design) — 2026-06-20

> Historical record — this document records the design as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

**Author:** mavedb-link maintainer (+ AI pair)
**Supersedes context:** round-3 HGVS-first / VRS-trim / gene-mirror work

## Problem

The CC0 MaveDB Zenodo bulk dump (concept `11201736`, current = record `18511521`,
**v4, 2026-02-06**) **omits the per-set annotations CSVs entirely**. Verified by
reading the zip's central directory: 3381 members = 2734 `.scores.csv` + 645
`.counts.csv` + `main.json` + 1 license file, **0 annotation members**. The
builder's expected name `csv/<urn>.annotations.csv` is correct; those members do
not exist.

Consequence: the mirror's `mapped_variant` table is empty, so the VRS/ClinGen
annotation layer is inert. The server already degrades correctly — every
VRS/mapped path transparently falls through to the live API, and the invariant
"mirror only changes latency/provenance, never shape" holds — so this is a
**coverage/latency** gap, not a correctness bug. But the round-2/round-3
investment (cross-dataset VRS rollup, `get_mapped_variants` mirror serving, the
HGVS→VRS fast-path) never lights up in production.

The live API **does** hold the mappings (`/score-sets/{urn}/mapped-variants` →
e.g. `00000001-a-1` = 12,720, `00000068-a-1` = 24,822 VRS alleles) but is slow
and flaky (timeouts, 504s; 12k–25k rows/set). Mapping coverage (free, from the
mirrored `mappingState`): `complete` 864 · `incomplete` 206 · `failed` 1660 ·
none 4 → only **~1,070 / 2,734** sets are mapped at all.

## Decision

Backfill the annotation layer **lazily, on call** (not as a batch build step):
a persistent write-through cache warms per score set the first time a tool
touches it, then serves mirror-fast on repeats. This keeps the monthly CI build
offline and avoids preloading ~2.6M rows; the working set of actually-used score
sets warms naturally.

The cache **follows the fleet convention** established by
`metadome-link/cache/store.py` (on-disk SQLite `ResultCache` + in-memory
`TTLCache` LRU front, `(id, data_version)` keying, WAL, JSON blob, `fetched_at`,
UPSERT, `stats`/`clear`/`close`, a `settings.cache` section, a dedicated cache
CLI) plus the **single-flight coalescing** refinement from
`panelapp-link/services/cache.py`.

## Architecture

### Components (units, boundaries, dependencies)

1. **`mavedb_link/data/mapped_cache.py` — `MappedVariantCache` (new, data plane).**
   On-disk SQLite store of raw live mapped-variant lists, keyed by
   `(score_set_urn, data_version)`, with a `TTLCache` LRU front (reused/copied
   from the metadome shape; disk authoritative, LRU `ttl=inf`).
   - Schema (one table; metadome-faithful):
     ```sql
     CREATE TABLE IF NOT EXISTS mapped_variants (
         score_set_urn TEXT NOT NULL,
         data_version  TEXT NOT NULL,
         fetched_at    TEXT NOT NULL,
         json          TEXT NOT NULL,        -- the raw live mapped-variant list
         PRIMARY KEY (score_set_urn, data_version)
     );
     ```
     A present row = "enriched" (even `json = "[]"` records "fetched, zero
     mappings" — distinct from "not yet fetched" = no row). This is why we store
     the whole list rather than per-variant rows: it serves **all** response
     modes of `get_mapped_variants` (the raw items shape exactly like live), and
     the per-variant/HGVS lookups scan the relevant set(s) in Python (the set is
     always known and bounded in those paths).
   - API: `get(urn) -> list[dict] | None`, `put(urn, items)`,
     `is_cached(urn) -> bool`, `stats() -> {on_disk, lru_size, data_version}`,
     `clear() -> int`, `close()` (idempotent).
   - `data_version`: `f"{MAPPED_CACHE_VERSION}:{mirror_token}"` where
     `mirror_token` = the mirror's `zenodo_version` (or `dump_as_of`) when a
     mirror is present, else `"live"`. So a mirror refresh to a new dump
     auto-invalidates stale cache entries; `MAPPED_CACHE_VERSION` (constant) bumps
     on any cache-shape change.

2. **`HybridClient` (extended) — the enrichment seam + single-flight.**
   Owns an optional `MappedVariantCache` (constructed beside the mirror repo;
   closed in `aclose()`). New async primitive:
   ```
   async def ensure_mapped_variants(self, score_set_urn) -> list[dict]:
       # 1. cache.get(urn) -> hit? return it.
       # 2. per-urn asyncio single-flight lock (coalesce concurrent cold fetches).
       # 3. re-check cache under lock.
       # 4. mappingState (mirror record) failed/none -> put(urn, []) ; return [].
       # 5. else super().get_json(/score-sets/{urn}/mapped-variants) -> list
       #        -> cache.put(urn, raw) ; return raw.   (provenance: live)
   ```
   The four mirror annotation reads consult **cache ∪ mirror**:
   - `score_set_mapped_variants(urn)` — cache list (or mirror) → upstream shape.
   - `mapped_vrs_for_variant(variant_urn)` — scan the cached list of the
     variant's own set (set = `score_set_urn_of_variant`), else mirror.
   - `vrs_for_hgvs(core, full, gene)` — target-relative HGVS: mirror
     `hgvs_index` → variant_urns (scoped by gene), VRS from the cached sets;
     genomic/accessioned HGVS: scan cached sets' `postMapped` HGVS; union, else
     mirror. (Needs a new read-only repo helper `hgvs_variant_urns(core, gene)`
     that returns `(variant_urn, score_set_urn)` from `hgvs_index` without the
     `mapped_variant` VRS join.)

3. **Service / resolver triggers (async seams that call `ensure_mapped_variants`).**
   - `MaveDBService.get_mapped_variants(urn)` — `await ensure_mapped_variants`;
     serve from the cache list (all modes), else live (unchanged fallback).
   - `resolvers.find_variant(variant_urn=)` → enrich the variant's set, then
     `mapped_vrs_for_variant`.
   - `resolvers.find_variant(hgvs=, gene=)` → enrich the gene's score sets
     (capped at `HGVS_PROBE_CAP`), then `vrs_for_hgvs`; the existing
     `_live_probe_hgvs` stays as the final fallback for a total miss.
   - `find_variant(vrs_id=)` cross-dataset bare-VRS rollup stays live (inherently
     global; relies on the existing HTTP response cache). Opportunistic cache use
     only if a containing set was already enriched. Documented, not silently
     truncated.

4. **Config — `CacheSettings` nested as `settings.cache`** (mirrors metadome):
   `db_path` (default `data/mavedb_cache.sqlite`), `enabled` (bool),
   `lru_sets` (LRU capacity, default e.g. 64). Honors the `MAVEDB_LINK_CACHE__*`
   env convention.

5. **CLI — `mavedb-link-cache` console-script** (typer; mirrors metadome's
   `metadome-link-cache`): `status` (stats) and `clear`. `warm` omitted (lazy by
   design).

6. **Diagnostics & docs honesty.**
   - `get_diagnostics` gains a `cache` block (`stats()`: on_disk, lru_size,
     data_version, enabled) and a `mirror.mapping_coverage` line
     (`complete/incomplete/failed/none` counts from `mappingState`, precomputed
     into `meta` at build time) so the empty `mapped_variant_count` is explained,
     not a silent surprise.
   - Correct the now-false claims that the dump carries annotations CSVs:
     `ingest/downloader.py` docstring, `ingest/builder.py` docstring, `AGENTS.md`
     "Source"/"Build" bullets, `README.md`. State plainly: the current export
     omits the annotations layer; the VRS/ClinGen layer is backfilled lazily from
     the live API into the on-disk cache (mirror-fast on repeat), with live
     fallback.

### Data flow (warm vs cold)

```
get_mapped_variants(urn) / find_variant(variant_urn|hgvs+gene)
        │
        ├─ ensure_mapped_variants(urn)
        │     ├─ cache hit ───────────────► serve from cache  (fast; data_source=live, cache stat++)
        │     └─ miss → single-flight lock
        │             ├─ mappingState failed/none → put([]) ► authoritative empty
        │             └─ live GET /…/mapped-variants → put(raw) ► serve  (first call pays live)
        └─ (bare vrs_id rollup) ─────────► live /mapped-variants/vrs/{id} (HTTP-cached)
```

### Provenance

A cache hit serves **live-derived** data, so `_meta.data_source` stays `live`
(data authority is the live snapshot); the latency/serving win is surfaced via
`get_diagnostics.cache` stats rather than by overloading the `mirror|live|mixed`
enum. (If the provenance tests make a distinct `cache` value cheap, prefer that;
decided at implementation.)

## Invariants / constraints

- **Output shape unchanged** — cache-served and live-served `get_mapped_variants`
  / `find_variant` payloads are byte-interchangeable (cache stores raw live
  items; the same shapers run). Verified in tests both ways.
- **Two-plane boundary** — the cache is data plane (plain dicts, typed
  exceptions); `run_mcp_tool` still owns envelopes. Tools unchanged; no new
  tools (`capabilities.TOOLS` frozen).
- **Read-only mirror untouched** — the immutable mirror stays `mode=ro`; the
  cache is a separate writable file.
- **Best-effort** — any cache read/write/enrich failure degrades to the existing
  live path (never fails a tool call). A `failed`/`none` mappingState set never
  triggers a live call.
- **600-LOC/module**, mypy strict, ruff, coverage ≥80, `make ci-local` green,
  `make eval` token/error gate respected (lazy warming must not change baseline
  payload shapes/sizes).
- **Single-flight** — concurrent identical cold enrich calls coalesce (one live
  fetch per set per window).

## Out of scope

- Batch/build-time preloading of all mapped sets (explicitly rejected by the
  user in favor of lazy on-call).
- Making cross-dataset bare-`vrs_id` rollup fully mirror-served (needs a global
  index; stays live + HTTP-cached).
- Background refresh of stale cache entries (invalidation is via `data_version`
  on mirror refresh + the cache CLI `clear`).

## Testing strategy

- Unit: `MappedVariantCache` (get/put/miss, `[]`-as-enriched, `(id,
  data_version)` invalidation, LRU front, stats/clear/close, WAL file created).
- Unit: `ensure_mapped_variants` (cold→live→cached; mappingState short-circuit;
  single-flight coalescing under `asyncio.gather`; best-effort on live error).
- Unit: the three find_variant triggers + get_mapped_variants serve from cache
  on the 2nd call (provenance/latency change, identical shape).
- Repo: `hgvs_variant_urns` over a hand-built mirror.
- Integration (respx): get_mapped_variants standard/full served from cache after
  one live fill; find_variant(hgvs, gene) warm path.
- Regression: `make eval` baseline unchanged; `test_tool_names` unchanged.
