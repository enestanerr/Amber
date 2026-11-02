# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-11-03

Launch preparation: the repository is public.

### Added
- A Claude Code plugin marketplace in this repository. `/plugin install amber --marketplace
  enestanerr/Amber` adds two skills: `/amber:compile` takes a repeated workflow from task prompt
  to a scheduled, compiled program, and `/amber:heal` runs the doctor and the right repair.
- The README is now a usage guide: install from GitHub, a two-minute offline demo, five ways to
  use Amber with Claude (headless runs, interactive sessions, the Agent SDK, Claude Desktop through
  the proxy, the plugin), a step-by-step guide, configuration, the safety model, commands and
  troubleshooting.
- An animated demo (`docs/assets/demo.svg`) recorded from a real session by `scripts/demo.py`,
  which replaces the vhs tape; the raw asciicast is `docs/assets/demo.cast`.
- The "How it works" diagram as an SVG: the README's mermaid block did not parse on GitHub
  (`llm()` inside an unquoted label), and PyPI does not render mermaid at all.

### Changed
- The default `max_llm_tokens` is 40000 (was 20000): a first live judgment through the claude CLI
  measured ~27k tokens.
- CLI messages print paths relative to the working directory, not the user's home.
- `FORCE_COLOR` produces ANSI colours on Windows pipes too (CI logs, recordings).
- weekly-report's `make run` and `make heal` use weeks that have data (2026-W40, 2026-W41).
- README images and links are absolute, so the page also renders on PyPI.

### Fixed
- The example scenario scripts printed absolute paths.

## [0.1.0] - 2026-11-02

First release. Everything below was recorded, compiled, verified and run live with the
`claude` CLI 2.1.316 on a claude.ai subscription (no API key); measured numbers are in
`bench/results.md`.

### Fixed (found by the live runs)
- Claude CLI backend: structured answers need two turns (`--max-turns 1` always ended in
  `error_max_turns`); calls drop built-in tools, dynamic system-prompt sections and session files
  (a judgment's CLI overhead fell from ~24k to ~1.2k tokens); prompts go through stdin and a
  system-prompt file (Windows command-line limit); judgments run at `--effort low`; usage of a
  rejected first attempt is no longer dropped; the CLI's reported cost is recorded.
- Recording: `--strict-mcp-config` keeps account-level MCP connectors out of the agent's
  context and the trace; Claude Code's `ToolSearch` is skipped as harness bookkeeping; the
  user's `claude` flags (`--model`, `--setting-sources`, …) are stored and the fallback relaunches
  the agent with them, so it never falls back to the default model.
- Canonicalization: the MCP `{"result": …}` envelope is dropped on every path (Claude Code shows
  it to the agent as text, the live backend reads structured content), so recorded and live
  result shapes agree.
- Alignment: ISO week/ordinal/year-month dates become transforms when another date can derive
  them (the previous week no longer becomes a required input); a constant value prefers `Const`
  over a coincidental list-position `Ref`; variable-length batch arguments are one leaf filled by
  one hole; conditional-step predicates need two supporting traces and a flag-like value.
- Compile: transforms are called with `inputs` as the dict they were verified with; gate fills
  verify although a recording never contains a `reason`; a conditional step's judgment output is
  optional; declared input names may appear in prompts; `from typing import <names>` and
  `from __future__` are accepted in transforms (the `typing` module itself stays blocked).
- Runtime: judgments get whole results within a 40 KB context budget instead of 20-item lists;
  `amber.toml` budgets apply at run time without a recompile; nested models in tool arguments
  become plain JSON; judgment answers no longer mention context keys; MCP server subprocesses
  always get a stderr with a file descriptor.
- Doctor: statistics cover only the diagnosed version (a healed v2 is no longer degraded by v1's
  stops) and a hard result-shape finding reports the hard event's detail.
- Store: a sync no longer drops traces it re-indexes under another spelling of their path.

### Changed
- issue-triage uses batch writes (`add_labels(updates)`, `add_comments(comments)`), duplicates
  are always newer than their originals, and the recorded days have different duplicate sets.
- price-watch records a quiet day (A) and a price-update day (A2, new), names the browser tools
  in its task and disallows file-reading tools; its spec'd CSS-class drift is absorbed by the
  accessibility snapshot, so the self-healing demo is weekly-report's.
- `bench/run_bench.py --baseline existing` measures against the recordings on disk, with cost and
  exact/core fidelity; compiled example programs are no longer tracked.
- PyPI publishing in `release.yml` is opt-in (`PYPI_PUBLISH` repository variable).

### Added
- Launch prep (P10): `bench/run_bench.py` + `amber bench` + `make bench` (agent baseline vs
  compiled runs; `--offline` runs the protocol on the existing recordings with the recorded
  model backend), README per the launch skeleton, user docs under `docs/user/` (quickstart,
  concepts, generated CLI reference, adapters, writing MCP servers, FAQ), `scripts/demo.tape` +
  `scripts/demo_gif.sh`, root `make demo-offline`.
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

[Unreleased]: https://github.com/enestanerr/Amber/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/enestanerr/Amber/releases/tag/v0.1.1
[0.1.0]: https://github.com/enestanerr/Amber/releases/tag/v0.1.0
