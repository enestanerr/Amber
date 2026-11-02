# Benchmark results

- date: 2026-11-02T18:38:35+00:00
- commit: `b60e14b`
- command: `uv run python bench/run_bench.py --example weekly-report --runs 3 --offline`
- claude CLI: 2.1.316 (Claude Code)
- models (amber.toml): synthesis = "claude-sonnet-5-5", judgment = "claude-haiku-5-5"
- mode: OFFLINE: agent baseline taken from the recordings already in .amber/traces (synthetic fixtures unless you recorded live); compiled runs executed against the mock servers with the recorded model backend, so compiled llm tokens are a prompt-length estimate, not measured usage

| example | agent input tok (median) | compiled llm tok (median) | ratio | agent wall s | compiled wall s | fidelity | agent turns |
|---|---|---|---|---|---|---|---|
| weekly-report | 18,085 (18,085-18,122) | 1,669 (1,626-1,691) | 10.8x | 19.2 | 2.1 | 100% | 0 |

Only this table may be quoted as Amber's own numbers (CLAUDE.md #5).
