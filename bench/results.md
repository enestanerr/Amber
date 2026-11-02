# Benchmark results

- date: 2026-11-02T20:50:08+00:00
- commit: `06a95f3`
- command: `uv run python bench/run_bench.py --all --runs 3 --baseline existing`
- claude CLI: 2.1.316 (Claude Code)
- agent model (baseline recordings): claude-sonnet-5-5
- compiled program models (amber.toml): synthesis=claude-sonnet-5-5, judgment=claude-haiku-5-5
- mode: LIVE: agent baseline = the live `claude -p` recordings in each example's .amber/traces (origin record); compiled runs live against the mock servers, llm() through the claude CLI backend

| example | agent tokens (median, min-max) | compiled llm tokens (median, min-max) | ratio | agent $ | compiled $ | agent wall s | compiled wall s | llm calls | fidelity exact | fidelity core | agent turns |
|---|---|---|---|---|---|---|---|---|---|---|---|
| issue-triage | 153,944 (152,975-160,124) | 11,892 (11,601-12,423) | 12.9x | $0.087 | $0.003 | 15.3 | 10.3 | 1 | 67% | 100% | 0 |
| price-watch | 165,225 (109,602-165,685) | 14,862 (7,307-14,889) | 11.1x | $0.071 | $0.003 | 21.5 | 11.4 | 2 | 100% | 100% | 0 |
| weekly-report | 174,076 (172,742-177,469) | 29,472 (27,498-30,648) | 5.9x | $0.170 | $0.008 | 28.9 | 33.6 | 1 | 0% | 100% | 0 |

Tokens: agent = input incl. cache reads/writes + output per recorded run; compiled = all
`llm()` input incl. cache + output incl. thinking per run. `$` is the API-equivalent
cost the claude CLI reports (a claude.ai subscription is not billed per call).
Fidelity: share of compiled runs whose tool sequence equals the recording for the same
input and scenario; *core* ignores the agent's exploratory reads that alignment pruned.

Only this table may be quoted as Amber's own numbers (CLAUDE.md #5).
