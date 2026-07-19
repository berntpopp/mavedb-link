# Lazy Mapped-Variant Cache — Implementation Plan

> Historical record — this document records the implementation plan as of its date. Current
> behavior is defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** TDD task-by-task. Steps use `- [ ]` checkboxes.

**Goal:** Backfill the dump-omitted VRS/ClinGen annotation layer lazily, on call,
via a persistent on-disk write-through cache that warms per score set and serves
mirror-fast on repeat — following the fleet `metadome-link` cache convention +
`panelapp-link` single-flight.

**Architecture:** `MappedVariantCache` (SQLite + LRU front, `(urn, data_version)`
key) owned by `HybridClient`; one async `ensure_mapped_variants(urn)` primitive
(single-flight, mappingState-gated) that `get_mapped_variants` and the
`find_variant` variant_urn/hgvs paths route through; cache∪mirror feeds the four
annotation reads; live fallback unchanged.

**Tech Stack:** Python 3.12+, sqlite3, asyncio, typer, pydantic-settings, respx
(tests), uv.

## Global Constraints

- ≤600 LOC/module (`make lint-loc`); mypy strict; ruff (line 100); coverage ≥80.
- `make ci-local` green + `make eval` baseline unchanged before handoff.
- Two-plane boundary: data plane returns dicts / raises typed exceptions; MCP
  plane owns envelopes. No new tools (`capabilities.TOOLS` frozen).
- Output shape invariant: cache-served ≡ live-served payloads.
- Best-effort: any cache/enrich failure degrades to the live path; never raises
  into a tool.
- Follow `metadome-link/cache/store.py` conventions verbatim where applicable
  (WAL, `(id, data_version)`, JSON blob, `fetched_at`, UPSERT, stats/clear/close,
  `settings.cache`, cache CLI).

---

### Task 1: `CacheSettings` config + constants

**Files:**
- Modify: `mavedb_link/config.py` (add `CacheSettings`, nest as `settings.cache`)
- Modify: `mavedb_link/constants.py` (add `MAPPED_CACHE_VERSION`, `MAPPED_CACHE_LRU_SETS`)
- Test: `tests/unit/test_config.py` (or extend existing)

**Interfaces — Produces:** `settings.cache.{enabled, db_path, lru_sets}`;
`constants.MAPPED_CACHE_VERSION: str`, `MAPPED_CACHE_LRU_SETS: int`.

- [ ] Step 1: Failing test — `settings.cache.db_path` default `data/mavedb_cache.sqlite`, `enabled=True`, `lru_sets` int; env `MAVEDB_LINK_CACHE__ENABLED=false` disables.
- [ ] Step 2: Run → fail (no attribute `cache`).
- [ ] Step 3: Add `CacheSettings(BaseModel)` (`enabled: bool=True`; `db_path: Path = data/mavedb_cache.sqlite`; `lru_sets: int = MAPPED_CACHE_LRU_SETS`) + `cache: CacheSettings = Field(default_factory=...)` on `ServerSettings`; add constants (`MAPPED_CACHE_VERSION = "1"`, `MAPPED_CACHE_LRU_SETS = 64`).
- [ ] Step 4: Run → pass.
- [ ] Step 5: Commit `feat(config): cache settings + mapped-cache constants`.

---

### Task 2: `MappedVariantCache` (SQLite + LRU front)

**Files:**
- Create: `mavedb_link/data/mapped_cache.py`
- Test: `tests/unit/test_mapped_cache.py`

**Interfaces — Produces:**
- `TTLCache[K,V]` (copy metadome shape: `get/set/clear/size`, `maxsize`, `ttl`, injectable clock) — or import a shared one if present (none in mavedb today → copy, small).
- `class MappedVariantCache(db_path, *, data_version, lru_sets=...)`:
  `get(urn)->list[dict]|None`, `put(urn, items: list[dict])`, `is_cached(urn)->bool`, `stats()->dict`, `clear()->int`, `close()`.

**Key code (store):**
```python
_SCHEMA = """CREATE TABLE IF NOT EXISTS mapped_variants (
  score_set_urn TEXT NOT NULL, data_version TEXT NOT NULL,
  fetched_at TEXT NOT NULL, json TEXT NOT NULL,
  PRIMARY KEY (score_set_urn, data_version));"""
# connect(check_same_thread=False); PRAGMA journal_mode=WAL; execute(_SCHEMA)
# get: LRU first; else SELECT json WHERE urn AND data_version; json.loads; warm LRU
# put: UPSERT (ON CONFLICT(score_set_urn,data_version) DO UPDATE), fetched_at=now UTC; LRU.set
# is_cached: SELECT 1 ...  ; stats: COUNT(*) for data_version + lru.size + data_version
```

- [ ] Step 1: Failing test — put/get round-trips a list; miss→None; `[]` round-trips as `[]` (not None) and `is_cached` True; a different `data_version` misses; LRU returns without touching disk (delete the file mid-test → LRU still serves); `clear()` returns count + empties; `close()` idempotent; WAL file appears.
- [ ] Step 2: Run → fail.
- [ ] Step 3: Implement `mapped_cache.py` (TTLCache + MappedVariantCache) per metadome shape.
- [ ] Step 4: Run → pass; `wc -l` < 600.
- [ ] Step 5: Commit `feat(cache): on-disk MappedVariantCache (metadome-pattern)`.

---

### Task 3: read-only repo helper `hgvs_variant_urns`

**Files:**
- Modify: `mavedb_link/data/repository.py`
- Test: `tests/unit/test_repository_hgvs.py` (extend)

**Interfaces — Produces:** `MirrorRepository.hgvs_variant_urns(core, *, gene=None) -> list[dict]` → rows `{variant_urn, score_set_urn}` from `hgvs_index` matched on `core` (the lowercased prefix-stripped body), optionally scoped by gene — i.e. `resolve_hgvs`'s target-relative arm **without** the `mapped_variant` VRS join (VRS comes from the cache).

- [ ] Step 1: Failing test — over the hand-built v2 mirror, `hgvs_variant_urns("p.asp2723his", gene="BRCA1")` → `[{variant_urn: urn:mavedb:1-a-1#1, score_set_urn: urn:mavedb:1-a-1}]`; unscoped spans genes.
- [ ] Step 2: Run → fail.
- [ ] Step 3: Implement (SELECT variant_urn, score_set_urn FROM hgvs_index [JOIN gene_index] WHERE hgvs_nt/pro/splice = core).
- [ ] Step 4: Run → pass.
- [ ] Step 5: Commit `feat(repo): hgvs_variant_urns (VRS-less hgvs_index lookup)`.

---

### Task 4: `ensure_mapped_variants` + single-flight on `HybridClient`

**Files:**
- Modify: `mavedb_link/data/hybrid.py` (own the cache; add primitive; cache∪mirror reads)
- Modify: `mavedb_link/mcp/service_adapters.py` (construct cache, pass to HybridClient)
- Test: `tests/unit/test_hybrid_enrich.py` (new)

**Interfaces — Consumes:** `MappedVariantCache`, `MirrorRepository`,
`super().get_json`. **Produces:**
- `HybridClient(config, *, repository, cache: MappedVariantCache | None = None)`
- `async ensure_mapped_variants(self, urn) -> list[dict]`
- cache-aware: `score_set_mapped_variants`, `mapped_vrs_for_variant`,
  `vrs_for_hgvs` now consult cache ∪ mirror.
- `mapped_cache_stats() -> dict | None` (for diagnostics); `aclose()` closes cache.

**Key code (single-flight + mappingState gate):**
```python
self._inflight: dict[str, asyncio.Lock] = {}
async def ensure_mapped_variants(self, urn):
    if self._cache is None: ...fetch live, return (no persist)
    hit = self._cache.get(urn)
    if hit is not None: return hit
    lock = self._inflight.setdefault(urn, asyncio.Lock())
    async with lock:
        hit = self._cache.get(urn)
        if hit is not None: return hit
        rec = self._repo.score_set_record(urn) if self._repo else None
        if (rec or {}).get("mappingState") in (None_marker...):  # 'failed' or absent-mapping
            items = []
        else:
            raw = await super().get_json(f"/score-sets/{urn}/mapped-variants")
            items = raw if isinstance(raw, list) else (raw.get("mappedVariants") or [])
        try: self._cache.put(urn, items)
        except Exception: pass
        return items
    # cleanup self._inflight.pop(urn, None) when no waiters (best-effort)
```
Gate rule: only `mappingState in {failed, None}` short-circuits to `[]` **without**
a live call; `complete`/`incomplete` (and unknown-but-present) fetch live.

- [ ] Step 1: Failing tests — cold call hits live once then caches (2nd call: no live call, same items); `mappingState='failed'` → `[]`, **zero** live calls; concurrent `asyncio.gather` of 5 identical cold calls → exactly **one** live fetch (single-flight); live error → returns live-less `[]`/raises per best-effort (assert no crash, falls back). Use a fake client recording call counts.
- [ ] Step 2: Run → fail.
- [ ] Step 3: Implement; wire `service_adapters._build_service` to build `MappedVariantCache(settings.cache.db_path, data_version=_data_version(repo))` when `settings.cache.enabled`, pass into HybridClient; `_data_version` = `f"{MAPPED_CACHE_VERSION}:{repo.meta().get('zenodo_version') or 'live'}"`.
- [ ] Step 4: Run → pass; `wc -l hybrid.py` < 600.
- [ ] Step 5: Commit `feat(hybrid): lazy ensure_mapped_variants (single-flight, mappingState-gated)`.

---

### Task 5: `get_mapped_variants` serves all modes from cache

**Files:**
- Modify: `mavedb_link/services/mavedb_service.py`
- Test: `tests/unit/test_mapped_variants_cache.py` (new) + adjust existing GAP-B tests

**Interfaces — Consumes:** `client.ensure_mapped_variants`. The live-fetch branch
becomes: `items = await self._client.ensure_mapped_variants(urn)` when the client
exposes it (duck-typed), else the current `await get_json(...)`. The existing
`_mirror_mapped_variants` (compact/minimal current-only from the immutable mirror)
stays as a first fast-check for future dumps that DO carry annotations.

- [ ] Step 1: Failing test (respx) — first `get_mapped_variants(urn, response_mode="standard")` hits live `/…/mapped-variants` once; second identical call returns the **same shape** with **no** further live call; `current_only` filter still applied; `full` mode also served from cache.
- [ ] Step 2: Run → fail.
- [ ] Step 3: Implement the duck-typed `ensure_mapped_variants` branch.
- [ ] Step 4: Run → pass (+ existing mapped-variant tests still green).
- [ ] Step 5: Commit `feat(service): get_mapped_variants served from lazy cache (all modes)`.

---

### Task 6: `find_variant` variant_urn + hgvs warm paths

**Files:**
- Modify: `mavedb_link/services/resolvers.py` (minimal: trigger enrichment then existing sync lookups)
- Modify: `mavedb_link/data/hybrid.py` if helper placement needs it (keep resolvers lean — LOC budget 500/600)
- Test: `tests/unit/test_resolvers_hgvs.py` (extend) + `tests/unit/test_tools_find_variant.py`

**Interfaces — Consumes:** `client.ensure_mapped_variants`, cache-aware
`mapped_vrs_for_variant` / `vrs_for_hgvs`. Logic:
- `_vrs_from_variant`: before the mirror fast-path, `await ensure_mapped_variants(score_set_urn_of_variant(urn))` (duck-typed) so the cached set feeds `mapped_vrs_for_variant`.
- `_vrs_from_hgvs`: on a mirror miss **with gene**, enrich the gene's sets (capped `HGVS_PROBE_CAP`) via `asyncio.gather(*ensure_mapped_variants(...))`, retry `vrs_for_hgvs` (now cache∪mirror); only on still-empty fall to `_live_probe_hgvs`.

- [ ] Step 1: Failing tests — `find_variant(variant_urn=…)` 2nd call resolves VRS from cache (no variant fetch); `find_variant(hgvs=, gene=)` warms the gene's sets then resolves from cache on repeat (probe not used on 2nd call); ambiguity/`gene`-required behaviors unchanged.
- [ ] Step 2: Run → fail.
- [ ] Step 3: Implement; keep resolver edits minimal (helper in hybrid/mapped_cache if LOC tight).
- [ ] Step 4: Run → pass; `wc -l resolvers.py` < 600.
- [ ] Step 5: Commit `feat(find_variant): lazy-cache warm paths for variant_urn + hgvs`.

---

### Task 7: diagnostics (`cache` block + `mapping_coverage`) + build-time coverage

**Files:**
- Modify: `mavedb_link/ingest/builder.py` (compute `mapping_coverage` from `mappingState`, store in `meta`), `mavedb_link/data/schema.sql` (+`mapping_coverage_json` column on `meta`; bump `MIRROR_SCHEMA_VERSION` → 3), `mavedb_link/constants.py`
- Modify: `mavedb_link/services/mavedb_service.py` (diag), `mavedb_link/data/hybrid.py` (`mirror_status` + cache stats)
- Test: `tests/unit/test_hybrid.py` (diag), `tests/unit/test_ingest_*` (coverage)

**Interfaces — Produces:** `get_diagnostics.cache = {enabled, on_disk, lru_size, data_version}`; `get_diagnostics.mirror.mapping_coverage = {complete, incomplete, failed, none}`.

- [ ] Step 1: Failing tests — builder writes `mapping_coverage` into meta; `mirror_status` surfaces it; diagnostics include a `cache` block.
- [ ] Step 2: Run → fail.
- [ ] Step 3: Implement; bump `MIRROR_SCHEMA_VERSION = 3`; update `_write_meta`/`_populate` to count mappingState; `mirror_status` reads it; service diag adds cache stats (duck-typed `mapped_cache_stats`).
- [ ] Step 4: Run → pass.
- [ ] Step 5: Commit `feat(diagnostics): cache stats + mirror mapping_coverage (schema v3)`.

---

### Task 8: `mavedb-link-cache` CLI

**Files:**
- Create: `mavedb_link/data/cache_cli.py` (typer: `status`, `clear`) — or fold into `mapped_cache.py:main` like metadome
- Modify: `pyproject.toml` (`[project.scripts] mavedb-link-cache = "..."`)
- Test: `tests/unit/test_cache_cli.py`

- [ ] Step 1: Failing test — `status` prints data_version/on_disk/lru_size; `clear --yes` empties.
- [ ] Step 2: Run → fail.
- [ ] Step 3: Implement (typer app; `ResultCache`-style construction from `settings.cache`).
- [ ] Step 4: Run → pass.
- [ ] Step 5: Commit `feat(cli): mavedb-link-cache status/clear`.

---

### Task 9: docs honesty + conftest reset + full gate

**Files:**
- Modify: `mavedb_link/ingest/downloader.py` + `ingest/builder.py` docstrings (dump omits annotations), `AGENTS.md`, `README.md`, `mavedb_link/mcp/capabilities.py` (note lazy VRS backfill)
- Modify: `tests/conftest.py` (autouse: clear/close the test cache; use a tmp_path db so tests never touch `data/`)
- Test: full `make ci-local` + `make eval`

- [ ] Step 1: Update docstrings/AGENTS/README/capabilities to state: current export omits the annotations CSVs; VRS/ClinGen backfilled lazily from live into the on-disk cache (mirror-fast on repeat), live fallback; cite verified zip evidence.
- [ ] Step 2: conftest — point `settings.cache.db_path` at a tmp file per session and reset between tests (mirror the existing cache-reset autouse fixture).
- [ ] Step 3: `make format lint typecheck lint-loc` → green.
- [ ] Step 4: `make ci-local` (full) → green; `make eval` baseline unchanged (regenerate only if an intentional surface change — none expected).
- [ ] Step 5: Commit `docs+test: lazy-cache docs honesty + test isolation`.

---

## Self-Review

- **Spec coverage:** cache store (T2), config (T1), enrichment+single-flight (T4),
  get_mapped_variants all-modes (T5), find_variant warm paths (T6), repo helper
  (T3), diagnostics+coverage (T7), CLI (T8), docs+isolation (T9). All spec
  sections mapped.
- **Types:** `ensure_mapped_variants` returns `list[dict]` everywhere; cache
  `get`→`list|None`, `put(list)`; `hgvs_variant_urns`→`list[dict]`. Consistent.
- **LOC risk:** resolvers.py (≈500) — keep T6 edits to `await` calls only; push
  any helper into hybrid/mapped_cache. hybrid.py (≈197) + repository.py (≈306) +
  mavedb_service.py — headroom OK; recheck after T4/T7.
- **Provenance:** cache hit = `data_source` `live` (data authority), cache
  effectiveness via diagnostics — avoids enum churn (revisit only if a `cache`
  value is cheap against provenance tests).
- **Schema bump:** T7 bumps `MIRROR_SCHEMA_VERSION` → 3 (old mirrors auto-reject →
  degrade to live, then re-pull); note in handoff that a mirror rebuild/republish
  is needed for `mapping_coverage` to populate (cache works regardless).
