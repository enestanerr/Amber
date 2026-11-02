---
title: "03 — CLI specification"
tags: [amber/spec, cli]
phases: [P0, P2, P3, P4, P5, P6, P7]
status: spec
created: 2026-11-02
---
# 03 — CLI specification

Binary: `amber`. Framework: typer. Every command accepts `--json` (machine output on stdout,
human output suppressed) and honours `AMBER_HOME` (default `./.amber`) and `AMBER_LOG`.
Human output uses `rich` tables; never print secrets.

Exit codes: `0` ok · `1` error · `2` usage · `3` verify failed · `4` guard stopped the run ·
`5` doctor found drift.

## `amber init`
Creates `.amber/`, `amber.toml` (see `01-architecture.md`), `.gitignore` entries. Resolves
model IDs via `anthropic.models.list()` when `ANTHROPIC_API_KEY` is set; otherwise writes
documented defaults with a `# verify` comment. Idempotent.

## `amber record <workflow> [options] -- <agent command...>`
Record one agent run.

| option | meaning |
|---|---|
| `--input k=v` (repeatable) | declared inputs; stored in the trace; injected into the prompt unless `--no-inject-inputs` |
| `--mcp PATH` | MCP config JSON passed to the agent (`--mcp-config PATH` for `claude`) |
| `--allow PATTERN` (repeatable) | tool allow patterns (`--allowedTools` for `claude`) |
| `--permission-mode MODE` | passed through to `claude` |
| `--sdk` | use the Agent SDK adapter instead of wrapping the CLI (needs `[sdk]`): the `-p` prompt and `--mcp/--allow/--permission-mode` values are translated to `ClaudeAgentOptions` |
| `--from-stdin` | read stream-json from stdin instead of running a command (tests, piping) |
| `--tag TAG` | tags on the trace |
| `--status success\|failure` | override outcome status |

Behaviour: validates the agent command starts with `claude` (v0.1 supports only Claude Code;
other harnesses use `amber proxy` + `amber import --proxy`), injects flags, streams, saves,
prints:

```
Recorded weekly-report → t_20261102_1a2b3c
  tool calls      9   (shop 6 · slack 1 · builtin 2)
  judgment cands  1   (slack.post_message.text)
  tokens          in 18,900 (cached 12,000) · out 1,120 · $0.031 · 18.0 s
Next: record 1–2 more runs with different inputs, then `amber align weekly-report --show`
```

## `amber import <workflow> (<session.jsonl> | --latest | --proxy DIR) [--input k=v] [--prompt TEXT] [--status ...]`
Import a Claude Code transcript or proxy fragments into a trace. `--latest` picks the newest
non-agent `*.jsonl` in `~/.claude/projects/<encoded cwd>/`.

## `amber proxy --name <server> [--trace-dir DIR] -- <upstream command...>`
Run the recording MCP proxy (stdio). Used from the host's MCP config, not interactively.

## `amber traces ls [workflow]` · `amber traces show <id>` · `amber traces rm <id>`
`show` prints events as a step table (idx, tool, args preview, result preview, ms) and the
outcome; `--full` dumps JSON.

## `amber align <workflow> [--show] [--keep-unreferenced] [--include-failed]`
Builds `.amber/build/<workflow>/skeleton.json`. `--show` prints:

```
Skeleton weekly-report  (3 traces · 7 steps · 1 pruned)
 #  tool                     kind           provenance                          in
 1  shop.week_range          deterministic  week ← Input(week)                  3/3
 2  shop.list_orders         deterministic  since ← Ref(1,$.start) until ← Ref(1,$.end) 3/3
 3  shop.top_products        deterministic  since,until ← Ref(1) · n ← Const(5)  3/3
 4  <transform>              transform      rfm_shift ← f(Ref(2), Ref(3))        3/3
 5  slack.post_message       judgment       channel ← Const("#eng") · text ← llm   3/3
 –  builtin.Read (pruned)    read-only, unreferenced, present 1/3
Inferred inputs: week (string)   Volatile: $.orders[*].created_at
```

## `amber compile <workflow> [--refresh] [--max-repair N] [--model ID] [--dry-run]`
Codegen + hole synthesis + verify. `--dry-run` writes the program with unfilled holes and
exits 0 (for inspection). Output: `programs/<workflow>/v<N>/`. Prints hole fills, repair
iterations, verify table, and `usable: yes/no`.

## `amber verify <workflow> [--version N] [--trace ID] [--live-llm] [--unordered]`
Replays each source trace. Table: trace id · calls consumed/expected · llm steps · result ·
divergence (first mismatch with a 3-line diff). Exit 3 on any failure.

## `amber run <workflow> [--input k=v]... [--version N] [--dry-run] [--allow-writes] [--fallback agent|none] [--timeout S]`
Live execution. Default: latest usable version; writes are dry-run unless `--allow-writes`;
fallback `none`. Output: step log then summary (`status ok|stopped|failed`, steps, llm calls,
tokens, cost, duration, agent_turns=0). `--json` emits `RunResult`. Exit 4 on `GuardStop`.

## `amber doctor <workflow> [--runs N]`
Runs all checks; prints findings and the recommendation; exit 5 if `status != healthy`.

```
Doctor price-watch  → degraded
  ✗ schema_drift      playwright.browser_snapshot input schema changed (hash 3f1a → 9c02)
  ✗ result_shape      step 4 missing key $.price in 3/3 recent runs (hard)
  ✓ servers           2/2 reachable
  ✓ llm_health        0 validation failures in last 10 runs
Recommendation: fallback  — run `amber run price-watch --fallback agent`, then `amber compile price-watch --refresh`
```

## `amber programs ls [workflow]` · `amber programs show <workflow> [--version N]` · `amber programs diff <workflow> <v1> <v2>`
`diff` shows a unified diff of `program.py` and a manifest delta (tools, llm steps, inputs).

## `amber schedule add <workflow> --cron EXPR [--input k=v]...` · `amber schedule ls` · `amber schedule rm <id>` · `amber scheduler`
Scheduler is a foreground loop (`croniter`); logs to `.amber/logs/scheduler.log`. Document
cron/GitHub Actions alternatives in the user guide.

## `amber ui [--port 8787] [--open]`
Starts the panel (needs `[ui]` extra).

## `amber bench <example> [--runs N]` (thin wrapper around `bench/run_bench.py`)

## Global behaviour
- `--project PATH` (or `AMBER_PROJECT`) selects the project root containing `amber.toml`;
  default is the current directory (walk up to the nearest `amber.toml`, like git).
- Missing `amber.toml` → hint to run `amber init`.
- Model backend resolution order: `AMBER_MODEL_BACKEND` env → `[models].backend` in toml →
  auto (`ANTHROPIC_API_KEY` → anthropic; `claude` on PATH → cli; else error "no model backend:
  set ANTHROPIC_API_KEY or install/login the claude CLI").
- `--json` outputs are Pydantic models' `model_dump(mode="json")`; document each in
  `docs/user/cli.md` (generated).
