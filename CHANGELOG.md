# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Per-call research-use disclaimer: `_meta.unsafe_for_clinical_use` is now
  stamped `True` on every tool response -- success and error paths alike -- at
  every `response_mode` (`minimal`/`compact`/`standard`/`full`), including
  argument-binding failures raised by the MCP middleware. Previously the
  disclaimer was only static text (server capabilities, README); it is now
  also an in-band `_meta` flag per the fleet's Response-Envelope Standard v1.
  This is additive only -- no envelope keys were renamed, removed, or
  restructured, and no version literal changed.

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
