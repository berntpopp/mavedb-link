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
