# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Web panel (P9): `amber ui` — overview with token-savings tiles and charts, programs and
  versions (highlighted program.py, manifest, REPORT), runs with step timelines, doctor
  (run from the page), decisions ledger, trace viewer, JSON API twins.
- Doctor + fallback (P7): `amber doctor` (live schema/server probe, result-shape and guard
  statistics, llm health, staleness, program lint → healthy/degraded/broken with a
  recommendation, exit 5), and the fallback loop behind `amber run --fallback agent`
  (proposal → sentry vetoes → arbiter → ledger → retry / agent resume / abort); fallback traces
  are folded in by `amber compile --refresh`.
- Run (P6): live MCP backend, safe builtins, guards (allowlist, budgets, write-effect dry-run,
  result shape, llm output, schema drift), SQLite ledger, runner with 0 agent turns,
  `amber run` (exit 4 on guard stop), `amber schedule` + `amber scheduler`.
- Examples (P8): synthetic generators, mock Slack / shop / GitHub MCP servers, the
  weekly-report (`make demo-offline`), issue-triage and price-watch projects with
  `make demo` / `make drift` / `make heal`.
- Verify (P5): cassette replay of every source trace (volatile masking, recorded `llm()`
  values, divergence diffs with hints, `--unordered`, `--live-llm` agreement), compile marks
  versions usable only after replay passes; `amber verify` (exit 3 on failure).
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
