# Design: HGVS-first entry, VRS trim, mirrored gene hop

- **Date:** 2026-06-20
- **Status:** Implemented (2026-06-20). See `docs/plans/2026-06-20-hgvs-first-vrs-trim-gene-mirror.md`.
- **Area:** `mavedb_link/` (services, mcp, data, ingest)
- **Related:** `docs/specs/2026-06-19-mavedb-link-design.md`,
  `docs/specs/2026-06-20-surface-calibrations-design.md`

## Motivation

Three frictions surfaced during real cross-dataset use:

1. **No HGVS entry point.** `find_variant` requires a VRS id or `variant_urn`.
   A caller holding a bare HGVS string had to probe several score sets with
   `get_variant_score` (some 404'd) just to obtain a VRS id before the clean
   `find_variant` rollup. This contradicts the repo's consolidation bias
   ("resolve identifiers internally rather than forcing a map-first round-trip").
2. **VRS scaffolding outruns information value.** At `standard`/`full`,
   `shape_mapped_variant` emits both full `pre_mapped` and `post_mapped` VRS
   objects per hit. The `vrs_id` (post-mapped digest) is already present in
   `compact`; the nested objects dominate token cost without matching value.
3. **The gene-endpoint hop is the latency floor.** `get_gene_score_sets` blocks
   on a live `GET /genes/{symbol}` (~2.5 s mixed) while mirror reads are ~4 ms.

## Goals / non-goals

**Goals:** HGVS-first `find_variant`; a lean `standard` VRS projection; remove
the live floor from the gene-listing path. **Non-goals:** no new tools, no
unrelated refactors, no architecture change. The two-plane boundary
(services return dicts / raise typed exceptions; `run_mcp_tool` owns the
envelope) and the invariant *the mirror only changes latency/provenance, never
output shape* hold throughout.

## 1. HGVS-first `find_variant`

### Signature (additive, backward-compatible)

```python
find_variant(
    vrs_id=None, variant_urn=None, hgvs=None, gene=None,
    only_current=True, enrich=True, limit=25, offset=0, response_mode="compact",
)
```

Exactly one anchor of `vrs_id` / `variant_urn` / `hgvs`. `gene` is meaningful
only with `hgvs` (it scopes and disambiguates); supplied alongside another
anchor it is ignored with a soft `_meta` note. Supplying zero or more than one
anchor → typed `InvalidInputError`.

### Resolution order (mirror-first; data plane returns dicts / raises typed exc)

1. **Normalise + classify** the HGVS string: strip whitespace; tolerate
   `p.(Arg45Gln)` parentheses and 1-vs-3-letter amino acids; detect
   *genomic/accessioned* (`NC_/NM_/NP_...:g./c./p.`) vs *target-relative*
   (`c./p./n.` without a genomic accession — the MaveDB scores-CSV flavour).
2. **Mirror — target-relative:** look up the new `hgvs_index`
   (`score_set_urn, variant_urn, hgvs_nt, hgvs_pro, hgvs_splice`), scoped by
   `gene` via `gene_index`, → `variant_urn`s → `mapped_variant.vrs_id`.
3. **Mirror — genomic:** match the now-indexed
   `mapped_variant.post_mapped_hgvs_{g,c,p}` → `vrs_id` (`gene` an optional
   filter via `score_set_urn`/`gene_index`).
4. **Collect distinct VRS ids.** If more than one (isoform/target differences),
   roll up all and report them; `gene` typically narrows to one.
5. **Live-probe fallback (mirror miss / no mirror built):** `gene` is
   **required** here (cannot probe all of MaveDB) — its absence on a miss →
   `InvalidInputError` naming `gene`. Then `GET /genes/{gene}` (live) →
   score-set URNs → probe each (capped, default `HGVS_PROBE_CAP = 10`) by HGVS,
   reusing the existing `get_variant_score` resolution path → `variant_urn` →
   VRS; dedup. Set provenance `live`; if the cap truncated the probe, set
   `probe_truncated: true` and add a `_meta` note.
6. **Roll up** the resolved VRS id(s) through the **existing** VRS cross-dataset
   path (`/mapped-variants/vrs/{id}`), unchanged. With `enrich` (default) each
   hit still gains `score` + calibrated `classifications`.

### Output

`resolved_by` gains the value `"hgvs"`. Add `resolved_vrs: list[str]` (≥1) and
`hgvs_input: str`. When multiple VRS resolved, `vrs_id` reports the first and
`resolved_vrs` carries all; hits are the merged rollup across them.
`after_find_variant` next_commands are unchanged (open first hit's score set +
variant; page when truncated). `FIND_VARIANT_SCHEMA` is extended and stays
permissive (`additionalProperties: true`).

### Ambiguity & errors

- `hgvs` + `gene` resolving to a single VRS → the common clean case.
- `hgvs` (no gene) resolving to multiple distinct VRS across genes →
  `AmbiguousQueryError` (typed) listing the candidate `{gene, vrs_id}` pairs so
  the caller re-asks with `gene=`. (Mirror path only; the live-probe path
  already requires `gene`.)
- `hgvs` that matches nothing in mirror and gene yields no probe hit →
  `NotFoundError` with the normalised string echoed.

## 2. Re-tier VRS scaffolding (`shape_mapped_variant`)

- **minimal / compact:** unchanged — identity only (`variant_urn`,
  `variant_index`, `vrs_id`, `clingen_allele_id`, `current`).
- **standard (new, lean):** drop `pre_mapped`. Emit a *flattened genomic
  summary* extracted from `postMapped` via a defensive `_summarize_vrs` helper:
  `post_mapped: {assembly, sequence_id, start, end, ref, alt}` plus
  `vrs_version`, `alignment_level`, and `post_mapped_hgvs` (from the mirror
  `post_mapped_hgvs_*` columns) when present. `_summarize_vrs` tolerates VRS
  1.x/2.x shape differences and returns `{}` for anything it cannot parse —
  it never raises.
- **full:** unchanged — complete `pre_mapped` + `post_mapped` objects and all
  metadata (`vrs_version`, `mapping_api_version`, `alignment_level`).

This is a surface change to `standard` output → regenerate the eval baseline
(`make eval-baseline`) and call out the diff. Callers needing `pre_mapped` or
the full nested objects at standard must request `response_mode="full"`;
documented in `capabilities.response_mode_semantics`.

## 3. Mirror the gene-endpoint hop (`get_gene_score_sets`)

Union semantics are preserved; the work moves off the live path:

- **Score sets:** served from the mirror's `gene_index` when the mirror is
  present. In the mirror, `target_search` (`POST /score-sets/search` with
  `targets=`) already resolves to the identical URN set via
  `gene_score_set_urns`, so this removes the live dependency for the listing
  without changing its shape.
- **Identity:** new process-wide bounded-FIFO `_GENE_IDENTITY_CACHE` (same
  pattern as the HGVS-validation cache). Cache hit → instant. Miss → live
  `GET /genes/{symbol}`, **time-boxed** (short budget); on slow/down/error,
  degrade to a mirror-derived thin identity `{symbol, organism}` (from
  `gene_index`) and set `gene_identity_source: "mirror" | "live"`.
- **Provenance:** `data_source` is `mirror` (score sets + degraded/cached
  identity) or `mixed` (live identity fetched alongside mirror score sets).
- **Freshness:** a symbol with **no** `gene_index` rows (cold/unknown gene)
  still goes fully live as today, so records newer than the snapshot are
  reachable.

## 4. Mirror schema v1 → v2 (`MIRROR_SCHEMA_VERSION = 2`)

- New table `hgvs_index(score_set_urn, variant_urn, hgvs_nt, hgvs_pro,
  hgvs_splice)` with indexes on the three HGVS columns; populated in
  `builder._insert_score_set` from the already-parsed scores CSV.
- Add indexes on existing `mapped_variant.post_mapped_hgvs_{g,c,p}`.
- `MirrorRepository` gains `resolve_hgvs(hgvs, *, gene=None)`; `HybridClient`
  gains `vrs_for_hgvs(...)` and `gene_identity(symbol)` (both duck-typed like
  `mapped_vrs_for_variant`, so a plain live client falls through).
- Old v1 mirrors are auto-rejected by the existing `schema_version` check in
  `MirrorRepository.open` → the server degrades to live-only until rebuilt (no
  crash, no regression). The prebuilt `mavedb.sqlite.zst` artifact re-publishes
  on the next `data.yml` run; README/bundle notes updated.

## 5. Cross-cutting polish (focused scope)

- Descriptions for the new `hgvs` / `gene` args and the re-tiered `standard`;
  `capabilities.response_mode_semantics`; `mavedb://tools`.
- Provenance correctness on every new path (mirror vs live vs mixed).
- Token-budget: the trim should *reduce* `standard` payloads — asserted in eval.
- `capabilities.TOOLS` unchanged (no new tool); `test_tool_names.py` stays green.

## 6. Testing & CI (TDD)

- **Unit:** HGVS resolution — target-relative + genomic flavours, `gene`
  scoping, multi-VRS ambiguity, mirror-miss → capped live probe, missing-`gene`
  error; `shape_mapped_variant` standard summary + full-unchanged; gene-identity
  cache hit / miss / timeout / degrade.
- **Hybrid interchangeability:** mirror-vs-live shape parity for the new paths
  (`tests/unit/test_hybrid.py`).
- **Ingest:** `hgvs_index` populated from a fixture dump; schema-version bump
  rejects a v1 mirror.
- **Eval:** `make eval-baseline` regenerated; the gate asserts the standard-mode
  token reduction and no error-rate regression.
- **Final gate:** `make ci-local` (format, lint-ci, lint-loc 600-LOC budget,
  mypy strict, test-fast) + `make eval`.

## 7. Risks & mitigations

- **HGVS normalisation variance** (case, parentheses, 1-vs-3-letter aa) →
  normalising matcher + the live-probe fallback as the safety net.
- **`standard` output change** → callers needing `pre_mapped` use `full`;
  documented; eval diff reviewed.
- **Bundle must rebuild for v2** → mirror auto-disables gracefully until the
  artifact re-publishes; pure-live behaviour is unchanged.
- **600-LOC budget** → HGVS resolution lives in the data/services layers; if a
  module approaches the budget, split the resolver helpers rather than inlining.
```
