# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Compile (P4): deterministic codegen (`program.py`, `manifest.json`, replay test, REPORT.md),
  typed holes verified against recordings, structured-output synthesizer with repair loop and
  transform→judgment downgrade, sandbox and lint, program versions, model backends
  (anthropic / claude CLI / recorded); `amber compile`, `amber programs ls|show|diff`.
- Align (P3): Needleman-Wunsch sequence alignment, provenance mining (Input / Ref /
  Template / Const / Unknown), step classification with hole kinds, conditional predicates,
  dead-step pruning, inferred inputs, volatile paths; `amber align <workflow> --show`.
- Recorders (P2): `amber record` wrapping `claude -p … --output-format stream-json`
  (flag injection, live step log, failure traces, denial warnings, schema enrichment via
  `--mcp`), `--from-stdin`, `--sdk` (Agent SDK extra), `amber import` for Claude Code
  transcripts (`--latest`) and MCP proxy fragments (`--proxy`), and `amber proxy`, a
  harness-agnostic recording MCP server. Synthetic stream/transcript/proxy fixtures.
- Trace model and store (P1): canonical `Trace` models, JSON-path utilities,
  canonicalization (`canonicalize_args`, `args_fingerprint`, `normalize_result`), JSON +
  SQLite `TraceStore`, `amber traces ls|show|rm`, seeded weekly-report fixtures.
- Project bootstrap (P0): packaging, CLI skeleton (`amber --version`, `amber init`), lint,
  type-check, test and CI configuration.

[Unreleased]: https://github.com/enestanerr/Amber/compare/main...HEAD
