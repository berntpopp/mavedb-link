# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.3] - 2026-07-13

### Changed

- Adopt the GeneFoundry container-release caller workflow and code-only
  production image release configuration bound to the published MaveDB
  `data-2026-06-24` external mirror artifact.

## [0.4.2] - 2026-07-12

### Security

- Redact upstream HGVS-resolution failures before they reach caller-visible
  error envelopes, including the live fallback path.

## [0.4.1] - 2026-07-11

### Security

- Guard the FastMCP-core not-found reflection surface. FastMCP core (pinned
  `>=3.4.4,<4.0.0`) reflects the caller's OWN requested tool name / resource URI
  / prompt name back to the caller and to logs BEFORE any backend middleware
  runs. A new `mavedb_link/mcp/notfound_guard.py` closes every observed
  sub-surface with fixed, input-free messages built from constants only:
  a registry preflight in `on_call_tool` returns a name-free `not_found`
  envelope for an unknown tool (no `_meta.tool` echo); an `on_read_resource`
  boundary re-raises a URI-free `ResourceError`; a protocol-handler backstop
  wraps the raw CallTool/ReadResource/GetPrompt handlers (the only layer that
  covers the unknown-prompt echo, `Unknown prompt: '<name>'`, even though MaveDB
  registers no prompts); and a validation-log scrub filter neutralizes the
  FastMCP/MCP-SDK log records that echo the raw name/URI (`Tool cache miss for
  <name>`, `Handler called: ... <uri>`, and the root-logger `Failed to validate
  request: ...` for a malformed URI). Caller self-reflection surface (the hostile
  bytes are supplied by, and reflected to, the same caller) — defense in depth;
  research use only. No success schema or error-envelope shape changed.

## [0.4.0] - 2026-07-11

### Security

- Adopt Response-Envelope Standard v1.1 untrusted-content fencing for MaveDB
  depositor/curator prose. Externally sourced free text is now emitted as a typed
  `untrusted_text` object (`kind`/`text`/`provenance`/`raw_sha256`) at the MCP
  serialization boundary, so hosts and the router treat retrieved content as
  data — never instructions. NFC normalization strips only the ratified
  control/zero-width/bidirectional code points; scientific symbols, tabs, and
  newlines are preserved, and `raw_sha256` digests the pre-normalization bytes.
  Defense in depth; research use only.
- Enforce the v1.1 untrusted-text limits over the WHOLE response (fenced
  object-count and total bytes across all rows), applied after the token-budget
  guard at the envelope boundary. A breach returns a typed `response_too_large`
  error (new error code) rather than silently omitting content.
- Never echo attacker-influenceable upstream error bodies into caller-visible
  strings. Upstream 4xx/5xx response bodies are severed at the API client
  (`_raise_for_status` raises fixed, status-keyed messages; the body is neither
  surfaced nor logged), `get_hgvs_validation` returns a fixed rejection reason,
  and diagnostics no longer exposes raw upstream detail. A defensive
  `sanitize_message` strips the fence's forbidden control/zero-width/bidi/NUL
  code points from every caller-visible message/error string (error envelopes,
  diagnostics, HGVS validation) as belt-and-suspenders.

### Changed (BREAKING)

- The following MaveDB free-text fields change type from `string` to the v1.1
  `untrusted_text` object: `get_score_set` `short_description` / `abstract_text`
  / `method_text`; `get_experiment` `short_description` / `abstract_text` /
  `method_text`; `search_score_sets` (and `get_gene_score_sets`) row
  `short_description` (plus `abstract_text` / `method_text` in full mode);
  `get_collection` `description`; and the calibration ladder's
  `baseline_score_description` / `notes` wherever it is emitted
  (`get_score_set` `score_calibrations`, and the `calibrations` block of
  `get_variant_scores` / `get_variant_score` / `get_score_distribution`).
  Consumers that read these as bare strings must update to read `.text` from the
  typed object. No sibling field duplicates the prose. The tool output schemas
  declare the `kind: untrusted_text` literal, including list-item schemas for the
  discovery arrays (`search_score_sets`/`get_gene_score_sets`/`search_experiments`)
  and the calibration array items.

## [0.3.0] - 2026-07-10

### Security

- Enforce exact configurable Host and Origin allowlists across every HTTP
  route, with safe loopback defaults, wildcard rejection, explicit production
  proxy hosts, and native FastMCP protection in depth. FastMCP is upgraded to
  3.4.4 while preserving structured argument-validation error envelopes.

### Changed (BREAKING)

- Host and Origin admission is now default-deny outside the configured
  loopback values. Non-loopback and reverse-proxy deployments must list their
  exact public names in `MAVEDB_LINK_ALLOWED_HOSTS` and browser origins, when
  used, in `MAVEDB_LINK_ALLOWED_ORIGINS`.

## [0.2.0] - 2026-07-10

### Added

- Per-call research-use disclaimer: `_meta.unsafe_for_clinical_use` is now
  stamped `True` on every tool response -- success and error paths alike -- at
  every `response_mode` (`minimal`/`compact`/`standard`/`full`), including
  argument-binding failures raised by the MCP middleware. Previously the
  disclaimer was only static text (server capabilities, README); it is now
  also an in-band `_meta` flag per the fleet's Response-Envelope Standard v1.
  This is additive only -- no envelope keys were renamed, removed, or
  restructured, and no version literal changed.

### Security

- Hardened remote artifact ingestion with validated manual redirect handling,
  incremental metadata and download limits, trusted digest and size checks,
  streamed SHA-256 provenance, atomic installation, bounded decompression, and
  strict tar/ZIP member policies that reject traversal, duplicates, links, and
  special files.

## [0.1.2] - 2026-07-03

### Fixed

- MCP `initialize` now advertises the real package version in
  `serverInfo.version` instead of leaking the FastMCP framework version
  (previously `3.4.2`). The `FastMCP(...)` instance in
  `mavedb_link/mcp/facade.py` was constructed without a `version=` argument,
  so FastMCP fell back to its own framework version; it is now built with
  `version=__version__`. `/health` was already correct and is unchanged.

### Changed

- Single-source versioning: `pyproject.toml` `[project].version` is now the
  sole source of truth. `mavedb_link.__version__` is derived at import time
  from the installed distribution metadata (`importlib.metadata.version`)
  instead of a hardcoded literal, so a version bump only has to be made in one
  place. This keeps `pyproject.toml`, the installed metadata, `__version__`,
  and the advertised `serverInfo.version` a single value. A new
  `tests/unit/test_version_single_source.py` guard locks the invariant against
  future drift.
