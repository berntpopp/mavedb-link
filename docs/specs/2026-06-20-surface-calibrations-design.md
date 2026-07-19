# Surface MaveDB's Interpretation & Resolution Layers

- **Status:** implemented (P0+P1+P2; `make ci-local` + live integration green)
- **Date:** 2026-06-20

> Historical record — this document records the design as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Area:** `mavedb_link/` (services + mcp planes)
- **Delivery:** built directly via TDD, one commit per wave (P0/P1/P2); tool
  surface 11 → 15. No separate plan doc — the wave commits are the record.

## Problem

MaveDB already curates the decision-relevant interpretation layer for a score
set — ACMG criteria, OddsPath ratios, per-bin functional-classification
thresholds, baseline (wild-type) anchors — and exposes genome-mapped VRS alleles
that bridge one variant across every score set. The current MCP **drops all of
it**:

- `get_score_set` omits `scoreCalibrations` (the single most decision-relevant
  field) and `scoreRanges`.
- `get_variant_score` / `get_variant_scores` return a **naked float** with no
  scale, direction, or classification — uninterpretable, and in one observed
  case nearly inverted the conclusion (a score below the pathogenic anchor read
  as "near wild-type / functional").

A real investigation therefore took ~20 calls and reconstructed 13.5k rows into
histograms to recover calibration data MaveDB already serves in one field.

## Verified upstream facts (api.mavedb.org, 2026-06-20)

- `GET /score-sets/{urn}` carries `scoreCalibrations`: a **list** (0, 1, or N).
  `00001224` → 1 (primary), `00001242` → 2, `00001225` → `[]`.
  - Per calibration: `title`, `baselineScore` (WT anchor; **may be null**, e.g.
    ExCALIBR), `researchUseOnly`, `functionalClassifications[]`,
    `thresholdSources[]`.
  - Per classification: `label`, `functionalClassification` enum
    (`abnormal` | `normal` | `not_specified`), `range:[lo,hi]` (either bound
    `null` = unbounded), `inclusiveLowerBound`, `inclusiveUpperBound`, optional
    `acmgClassification:{criterion (PS3|BS3), evidenceStrength}`, optional
    `oddspathsRatio`, `variantCount`, `id`.
  - **Direction is not fixed**: `00001224` higher = normal (WT=5); `00001242`
    lower = abnormal (PS3 at negative scores). Ranges drive the mapping.
  - **Ranges may have gaps**: in `00001242` a score in `(-0.900, -0.580)` lands
    in no bin → indeterminate (the value the original review misread).
  - `scoreRanges` is **absent** on every record probed → optional passthrough.
- `GET /mapped-variants/vrs/{identifier}` (`only_current` query) → a **list** of
  mapped-variant rows for one allele **across all score sets** (one VRS id →
  3 distinct sets confirmed).
- `POST /hgvs/validate` body `{"variant": "<hgvs>"}` → bare `true` on valid; a
  descriptive `400` on invalid (`"reference (A) does not agree with reference
  sequence (G)"`); `500` on malformed. A **validator**, not a normalizer.
- `GET /score-calibrations/score-set/{urn}` → all calibrations with their own
  `urn` + classification `id`s (5 for `00001224`, vs. 1 primary embedded in the
  score-set record). `/score-calibrations/score-set/{urn}/primary` → the primary.
- `GET /score-calibrations/{calib_urn}/variants` → variants grouped by
  classification id, each with `score`/`hgvsNt`/`hgvsPro`;
  `.../functional-classifications/{id}/variants` → one class.
- MaveDB has **no per-set distribution/stats endpoint** → a distribution summary
  is genuine MCP value-add.

## Architecture

New pure module **`mavedb_link/services/calibration.py`** (data plane, no I/O):

- `shape_calibrations(raw: list, *, full: bool) -> list[dict]` — normalize the
  camelCase calibration list to tidy snake_case. Threshold sources shaped to
  `{db_name, identifier, title}`. `full` adds `notes`/`baseline_score_description`.
- `classify_score(score, raw_calibrations) -> list[dict]` — **range-driven,
  direction-agnostic, gap-aware**. For each calibration, find the bin containing
  `score`; emit `{calibration, classification, label, acmg, acmg_strength,
  oddspath, baseline_score}`; emit `classification="indeterminate"` when no bin
  matches; return `[]` when no calibrations or `score is None`.
- `primary_classification(score, raw_calibrations) -> str | None` — the single
  primary-calibration verdict, for per-row tagging.

Reused by `shaping.py` (score set), `mavedb_service.get_variant_score`,
`scores.py` (bulk rows), and the new resolver/distribution tools. Pure functions
match the existing `shaping.py` pattern and keep every module < 600 LOC.

The two-plane boundary is held: services return plain dicts / raise typed
exceptions; `run_mcp_tool` owns `success`/`_meta` and returns structured errors.

## Wave P0 — interpretation layer (enrich 3 existing tools)

1. **`get_score_set`**: add `score_calibrations = shape_calibrations(...)` to the
   compact-and-richer payload (auto-dropped by `_drop_empty` when `[]`), and
   `score_ranges` passthrough in standard/full. Extend `SCORE_SET_SCHEMA`.
2. **`get_variant_score`**: after resolving the variant's score + score-set URN
   (both the variant-URN and the hgvs-scan paths), fetch the score-set record
   (cached) and attach `classifications` (per-calibration list). Extend
   `VARIANT_SCORE_SCHEMA`. Each hgvs-scan match also gets `classifications`.
3. **`get_variant_scores`**: attach a lean `calibrations` thresholds block at the
   payload top level + a per-row `classification` string from the primary
   calibration (omitted when no calibrations). Extend `VARIANT_SCORES_SCHEMA`.

## Wave P1 — resolver tools (3 new tools)

New module `mavedb_link/mcp/tools/resolvers.py`; new client + service methods.

4. **`find_variant(identifier, only_current=True, enrich=True, limit, offset)`** —
   `GET /mapped-variants/vrs/{identifier}`. Returns the allele across every score
   set: `{score_set_urn, variant_urn, clingen_allele_id, vrs_id}`; when
   `enrich`, also `score` + `classifications`. `identifier` accepts a VRS id
   (`ga4gh:VA…`) or ClinGen id (`CA…`; resolution path verified in impl).
5. **`get_hgvs_validation(variant)`** — `POST /hgvs/validate`. Returns
   `{variant, valid, message}`; honest validator surfacing the failure reason.
6. **`get_classified_variants(urn, classification=None, calibration_urn=None,
   start, limit)`** — resolve the score set's primary calibration, return the
   variants in a functional class (e.g. every `abnormal`), with scores. Paged.

## Wave P2 — ergonomics (1 new tool)

7. **`get_score_distribution(urn, score=None)`** — pure compute over the scores
   CSV: `{n, min, max, median, mean, q1, q3, stdev, histogram}`; when `score`
   given, its `percentile` + calibrated `classifications`. Honest about any
   internal row cap (reported in the payload). The summary-not-13.5k-rows answer
   to the token-blowup.

## Per-tool wiring (every new tool, per CLAUDE.md)

`capabilities.TOOLS` + output schema + `after_*` chainer + registration in the
facade + `tests/unit/test_tool_names.py` expectations + server-instructions
(`resources.py`) + the `mavedb://tools` overview — updated together. Tool surface
grows 11 → 15. All names are canonical-verb `verb_noun`, ≤ 50 chars.

## Error handling

- Unknown VRS/ClinGen id, score set, or calibration → upstream `404` →
  `NotFoundError` → `not_found` envelope.
- Malformed HGVS (`500`) and out-of-range classification arg → `InvalidInputError`
  → `invalid_input` with a `hint`.
- `get_classified_variants` on a set with no calibrations → `not_found` with a
  hint to call `get_score_set` (which now shows whether calibrations exist).

## Testing (TDD)

- `tests/unit/test_calibration.py` — pure classifier: in-range, boundary
  inclusivity, null bounds, **gap → indeterminate**, multi-calibration, no-calib,
  `score is None`, both score directions.
- Extend `test_shaping.py`, `test_service.py`, `test_scores.py`,
  `test_output_schemas.py`, `test_tool_names.py`, `test_capabilities.py`,
  `test_next_commands.py`, `test_tools_e2e.py`; add `test_resolvers.py`.
- Fixtures (`tests/fixtures.py`) gain a calibrations block (incl. a gapped,
  null-baseline calibration) and mapped-variant/validate/classified samples.
- `make ci-local` (format-check, lint-ci, lint-loc, mypy strict, test-fast)
  green per wave; each wave is independently shippable.

## Addendum (2026-06-20) — token discipline in discovery listings

P0 added `score_calibrations` to `shape_score_set`, which is shared by the record
call (`get_score_set`) **and** the discovery listings (`search_score_sets`,
`get_gene_score_sets`). That leaked the full per-bin ACMG/OddsPath ladder into
*every* calibrated listing row: an observed `get_gene_score_sets(BRCA1)` self-
reported ~6.9k tokens, most of it inlined calibration tables with 16-significant-
digit bin edges (e.g. `-0.9092407272057206`), while an uncalibrated gene listed in
~1.6k. Calibration detail is record-level data.

Fix (shape-only; no new tool, two-plane boundary held):

- `shape_score_set(raw, mode, *, listing=False)`. Listings pass `listing=True`:
  the ladder is replaced by a `has_calibrations: true` presence flag (drill into
  `get_score_set` for the table), and `targets` collapses to gene-name strings
  (a multi-target assay no longer dumps a nested object per target per row). The
  record call (`listing=False`) is unchanged.
- All emitted calibration thresholds / OddsPath ratios / baselines round to 6
  significant figures (`calibration.round_sig`). **Range *matching* still uses the
  raw, unrounded thresholds** — only the displayed numbers are trimmed.
- Net: a two-calibrated-set listing drops ~71% (≈950 → ≈275 tokens); `get_score_set`
  and the per-variant interpretation tools are unaffected.

## Non-goals

- No write/auth paths (MaveDB reads are public; the router forwards no tokens).
- No per-variant molecular consequence (silent/missense) — absent upstream when
  uploaders leave `hgvs_pro` null; needs external annotation, out of scope.
- HGVS *normalization* (only validation exists upstream); `GET /hgvs/{transcript}`
  is flaky (502) and not wrapped.
