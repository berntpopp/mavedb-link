# Architecture

Design spec: [`specs/2026-06-19-mavedb-link-design.md`](specs/2026-06-19-mavedb-link-design.md).
Code-level conventions (the two-plane boundary, tool-naming rules, the consolidation bias)
live in [`AGENTS.md`](../AGENTS.md) and are not repeated here.

## The MaveDB data model

Everything the server returns hangs off MaveDB's URN hierarchy:

```
ExperimentSet (urn:mavedb:00000001)
  └─ Experiment      (urn:mavedb:00000001-a)
       └─ ScoreSet   (urn:mavedb:00000001-a-1)   ── TargetGene(s) ── Taxonomy
            └─ Variant (urn:mavedb:00000001-a-1#1) ── MappedVariant (VRS allele)
```

- **Variants** carry HGVS (`hgvs_nt` / `hgvs_pro` / `hgvs_splice`) and a quantitative `score`.
- **Mapped variants** project a variant onto a reference genome as a GA4GH VRS allele, with
  an optional ClinGen Allele ID. Upstream, scores download as CSV.
- **Licences are per score set** (CC0 1.0 / CC BY 4.0 / CC BY-SA 4.0) — see
  [data.md](data.md).

## The calibration layer

The reason this server exists. A raw functional score is an uninterpretable float: whether
`-1.8` counts as *abnormal* depends on the score set's own thresholds. MaveDB curates those
thresholds, and `mavedb-link` joins them to the scores rather than making the caller do it:

- `get_score_set` returns `score_calibrations` — the functional-classification thresholds with
  their ACMG **PS3/BS3 evidence strength** and **OddsPath** ratios.
- `get_variant_scores` stamps each row with its calibrated functional class.
- `get_variant_score` returns a per-calibration classification for one variant.
- `get_classified_variants` inverts the lookup: every variant in a given class (e.g. all
  `abnormal` / PS3).
- `get_score_distribution` places a query score in the distribution (quartiles, histogram,
  percentile) *and* its class.

A calibrated class is **assay evidence**, not a pathogenicity call. See the research-use
boundary in the [README](../README.md).

## Identifier resolution

`find_variant` anchors a cross-dataset lookup by any of three identifier forms:

1. a GA4GH VRS id,
2. a `variant_urn`, or
3. a bare `hgvs=` string (plus an optional `gene_symbol=`), resolved to VRS internally.

This is deliberate: the surface resolves identifiers internally wherever MaveDB supports it
rather than forcing a map-first round-trip on the caller.

`get_hgvs_validation` explains *why* an HGVS string is invalid (reference mismatch, missing
accession) instead of failing opaquely.

## Response contract

Every tool accepts `response_mode` ∈ `minimal` | `compact` | `standard` | `full`, defaulting
to **`compact`**. Start compact and widen only if needed — it is the token-cost lever.

Responses carry:

- `success` and a structured `_meta` block, owned by the MCP plane (`run_mcp_tool`).
- `_meta.next_commands` — ready-to-run follow-up `{tool, arguments}` steps, so a model can
  chain without guessing.
- `_meta.data_source` (`mirror` | `live` | `mixed`) and `_meta.mirror_as_of` — per-call
  provenance (see [data.md](data.md)).

Errors are **returned, never raised**, as structured error frames with a typed error code.

## Resources

The server registers a `mavedb://` resource family for discovery:

| Resource | Contents |
|----------|----------|
| `mavedb://capabilities` | Full capabilities payload (JSON) |
| `mavedb://tools` | Tool inventory and signatures (JSON) |
| `mavedb://usage` | Workflows and usage guidance |
| `mavedb://reference` | Domain reference notes |
| `mavedb://research-use` | The research-use restriction |
| `mavedb://citation` | The recommended citation, to paste verbatim |

`get_server_capabilities` returns the same discovery surface as a tool call.

## Transport

Streamable HTTP at `/mcp` (`--transport unified`), a REST/health-only mode
(`--transport http`), and `stdio` via `mcp_server.py`. The transport modes and their footguns
are documented in [configuration.md](configuration.md).
