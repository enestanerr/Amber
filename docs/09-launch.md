---
title: "09 — Benchmarks, README, GIF, release"
tags: [amber/spec, launch]
phases: [P10]
status: spec
created: 2026-11-02
---
# 09 — Benchmarks, README, GIF, release

## Benchmark protocol (`bench/run_bench.py`)

For each example (default all) and `--runs N` (default 3):

1. **Agent baseline**: `amber record` N times with the example's inputs (fresh traces,
   tagged `bench`). Collect from `Outcome`: `usage.total_input`, `usage.output_tokens`,
   `cost_usd`, `duration_ms`, `num_turns`, `calls`.
2. **Compiled**: ensure a usable version (compile from the non-bench traces); `amber run`
   N times with the same inputs (`--allow-writes` against mocks). Collect: llm tokens
   (input/output), cost, duration, calls, `agent_turns` (must be 0), and
   **tool-call fidelity** = fraction of runs whose tool-call sequence (canonical names + masked
   args) equals the recording for the same input.
3. Write `bench/results.json` (raw) and `bench/results.md`:

```
| example | agent input tok (median) | compiled llm tok (median) | ratio | agent wall s | compiled wall s | fidelity | agent turns |
```

plus a header with date, `claude --version`, model IDs, commit hash, and the exact command.
Only this table may be quoted in README as Amber's numbers. Do not compare against the
FlashScript paper's numbers in the same table; cite them separately as prior evidence.

## README skeleton

```
# Amber — compile your agent's runs into programs
<one-line value prop>  ·  badges (CI, PyPI, license, Python)

[demo.gif ≤ 60 s: record ×3 → align --show → compile → verify 3/3 → run (0 agent turns) → drift → doctor → heal]

## Why
3 bullets: cost/latency/determinism; the research result (FlashScript, cited); what Amber adds (open, harness-agnostic, replay tests, drift loop).

## Install
uv tool install amber-compiler      # or: pipx install amber-compiler
# needs: Python 3.12+, the `claude` CLI logged in OR ANTHROPIC_API_KEY

## 60-second story (6 commands)
amber record weekly-report --input week=2026-W44 --mcp mcp.json -- claude -p "..."
amber record ... (×2 with other weeks)
amber align weekly-report --show
amber compile weekly-report
amber verify weekly-report
amber run weekly-report --input week=2026-W45 --allow-writes
amber doctor weekly-report

## How it works
mermaid flowchart (record → align → compile → verify → run → doctor → fallback → recompile) + 1 paragraph per stage + the "deterministic compiler, typed holes" principle.

## What comes out
Show a trimmed program.py (from examples/weekly-report) and REPORT.md excerpt.

## Measured (bench/results.md table, date, model)

## Supported inputs
Claude Code (`claude -p`), Claude Code transcripts, Claude Agent SDK, any MCP server via `amber proxy`. Roadmap: OpenAI Agents SDK, OpenTelemetry GenAI spans.

## What it can't do yet (honest list)
loops/pagination collapse, parallel steps, multi-agent traces, most Claude Code builtins, hosted runner, approval UI, Windows (best effort)

## Related work (one line each, link)
muscle-mem · FlashScript (paper, no code) · taprun · Codex record-and-replay · AWO/tool-making papers

## FAQ (5–7 questions)
Is this a cache? Does it need an API key? What about side effects? Can I edit program.py? What models does it call at runtime? Does it send my data anywhere?

## Contributing · License (Apache-2.0)
```

Every claim in README must map to a test or a bench row. Add a `docs/user/` quickstart that
mirrors the 60-second story with expected output.

## Demo GIF (`scripts/demo_gif.sh`)

Use `vhs` (charmbracelet) tape `scripts/demo.tape`: 120×36 terminal, theme with good
contrast, type speed 40 ms, pauses at summaries. Sequence (≤ 60 s): `make demo` output
condensed (show the record summary once, then align table, compile hole fills, verify 3/3,
run summary with `agent turns 0`), then `make drift`, `amber run` stopping on a guard,
`amber doctor` recommendation, `make heal` ending with `verify 4/4 PASS v2`. Export GIF ≤ 8 MB
to `docs/assets/demo.gif`; also an MP4 for LinkedIn.

## Release checklist (v0.1.0)

- [ ] `CHANGELOG.md` entry; version in `pyproject.toml` and `amber/version.py`.
- [ ] `uv build`; `twine check dist/*`; install the wheel in a fresh venv and run `amber --version`, `amber init`.
- [ ] Tag `v0.1.0`; `release.yml` publishes via Trusted Publishing (configure the PyPI
      publisher for the repo first); GitHub Release notes = CHANGELOG + bench table + GIF.
- [ ] Verify `uvx --from amber-compiler amber --version` from a clean container.
- [ ] README links (docs, examples, bench) resolve; CI badge green.
- [ ] Open 6–10 "good first issue"s (adapters, loop detection, Windows paths, docs) before
      announcing.

## Post-launch (for the maintainer, not for Claude Code)

Show HN / r/MachineLearning / r/ClaudeAI / LinkedIn / X posts, awesome-list PRs (awesome-mcp-servers,
awesome-llm-apps, awesome-claude-code), a short technical write-up "What a compiler for agent
runs looks like", and a follow-up post with the bench table and the drift GIF.
