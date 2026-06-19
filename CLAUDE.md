# CLAUDE.md

@AGENTS.md

Claude Code entrypoint only:

- Use `AGENTS.md` for shared repository instructions.
- Keep Claude-specific additions here short and tool-specific.
- Prefer `make ci-local` before final handoff (runs `lint-loc`, the 600-LOC budget).
- FastMCP 3.x symbols are post-training-cutoff and fast-moving — verify imports
  against the installed package before relying on them.
- The two-plane boundary is non-negotiable: services return plain dicts + raise
  typed exceptions; `run_mcp_tool` owns `success`/`_meta` and returns (never
  raises) structured errors.
- When adding a tool, update `capabilities.TOOLS`, its output schema, an
  `after_*` chainer, and the `test_tool_names.py` expectations together.
