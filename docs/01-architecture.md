---
title: "01 — Architecture"
tags: [amber/spec, architecture]
phases: [P0, P1, P6]
status: spec
created: 2026-11-02
---
# 01 — Architecture

## Data flow

```
                 ┌──────────────── adapters ────────────────┐
 claude -p ──►   │ claude_stream   claude_transcript        │
 Agent SDK ──►   │ agent_sdk       mcp_proxy (any harness)  │ ──► Trace (JSON) ──► TraceStore
                 └───────────────────────────────────────────┘        │
                                                                      ▼
                                   align: sequence ─► provenance ─► classify ─► prune ─► Skeleton
                                                                      │
                                                                      ▼
                             compile: codegen (jinja2) ─► holes ─► synthesizer (model, repair loop)
                                                                      │
                                              programs/<wf>/vN/{program.py, manifest.json, tests/}
                                                                      │
                            verify: CassetteBackend + RecordedLLMBackend (replay each source trace)
                                                                      │
                 run: LiveBackend (MCP) + Builtins + ModelBackend(llm) + Guards ─► Ledger (SQLite)
                                                                      │
                    doctor: checks ─► Diagnosis            fallback: Proposal ─► Sentry ─► Arbiter
                                                                      │
                                                        agent_fallback ─► new Trace ─► compile --refresh
```

## Modules and ownership

| Package | Responsibility | May import |
|---|---|---|
| `amber.trace` | Canonical trace models, canonicalization, JSON-path utils, store | `amber.util` |
| `amber.adapters` | Convert harness-specific events into `Trace` | `amber.trace`, `mcp`, `claude_agent_sdk` (optional) |
| `amber.align` | Skeleton construction from traces | `amber.trace` |
| `amber.compile` | Manifest, codegen, holes, synthesizer, program versions | `amber.align`, `amber.models`, `amber.runtime.program_api` |
| `amber.runtime` | Program API, backends (cassette/live/builtins), guards, ledger, runner, verify, scheduler | `amber.trace`, `amber.compile.manifest`, `amber.models`, `mcp` |
| `amber.models` | `ModelBackend` implementations (anthropic API, claude CLI, recorded) | `anthropic` |
| `amber.doctor` | Drift checks, diagnosis | `amber.runtime`, `amber.compile.manifest` |
| `amber.fallback` | Proposal, sentry, arbiter, agent fallback | `amber.runtime`, `amber.adapters.claude_stream` |
| `amber.ui` | FastAPI panel | `amber.runtime.ledger`, `amber.compile` (read-only) |
| `amber.cli` | Typer commands; thin: parse → call a service function → render | everything |

Rule: `trace`, `align`, `compile`, `runtime` contain no Claude-specific strings or imports.
"Claude" appears only in `adapters/`, `models/`, `fallback/agent_fallback.py`.

## Storage

```
<project>/
├── amber.toml                    # project config (models, servers, budgets, defaults)
├── .amber/                       # runtime state (gitignored)
│   ├── amber.db                  # SQLite (WAL): traces index, runs, run_steps, guard_events, llm_calls, decision_records, schedules
│   ├── traces/<workflow>/<trace_id>.json
│   ├── build/<workflow>/skeleton.json
│   ├── proxy/<server>-<pid>.jsonl   # mcp proxy fragments
│   └── logs/
└── programs/<workflow>/v<N>/     # committed artifacts (the user decides; examples commit them)
    ├── program.py
    ├── manifest.json
    ├── REPORT.md
    └── tests/test_replay.py
```

`amber.toml`:

```toml
[project]
name = "my-workflows"

[models]
backend = "auto"          # auto | anthropic | cli | recorded
synthesis = "claude-sonnet-5-5"   # resolved by `amber init` via anthropic.models.list(); verify before trusting
judgment  = "claude-haiku-5-5"

[servers.shop]
command = "uv"
args = ["run", "examples/weekly-report/server_shop.py"]
env = { AMBER_SCENARIO = "A" }

[servers.slack]
command = "uv"
args = ["run", "examples/_shared/server_slack.py"]

[servers.playwright]
command = "npx"
args = ["-y", "@playwright/mcp@latest"]

[budgets]
max_calls_factor = 2.0     # × max recorded calls
max_llm_tokens = 20000
wall_clock_s = 600

[fallback]
daily_budget = 3
```

Trace IDs: `t_<yyyymmddHHMMSS>_<6 hex>`. Run IDs: `r_<...>`. Program versions: integers.

## SQLite schema (frozen at end of P6)

```sql
CREATE TABLE traces (id TEXT PRIMARY KEY, workflow TEXT, adapter TEXT, created_at TEXT,
  status TEXT, calls INTEGER, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
  origin TEXT, path TEXT);
CREATE TABLE runs (id TEXT PRIMARY KEY, workflow TEXT, version INTEGER, started_at TEXT,
  finished_at TEXT, status TEXT, mode TEXT, inputs TEXT, steps INTEGER, llm_calls INTEGER,
  llm_input_tokens INTEGER, llm_output_tokens INTEGER, cost_usd REAL, agent_turns INTEGER,
  stop_kind TEXT, stop_detail TEXT);
CREATE TABLE run_steps (run_id TEXT, idx INTEGER, kind TEXT, tool TEXT, args TEXT,
  result_digest TEXT, duration_ms INTEGER, status TEXT, PRIMARY KEY (run_id, idx));
CREATE TABLE guard_events (id INTEGER PRIMARY KEY, run_id TEXT, idx INTEGER, guard TEXT,
  severity TEXT, detail TEXT, ts TEXT);
CREATE TABLE llm_calls (id INTEGER PRIMARY KEY, run_id TEXT, idx INTEGER, step_id TEXT,
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, valid INTEGER, confidence REAL, ts TEXT);
CREATE TABLE decision_records (id INTEGER PRIMARY KEY, run_id TEXT, workflow TEXT, ts TEXT,
  proposal TEXT, vetoes TEXT, decision TEXT, reason TEXT, actor TEXT);
CREATE TABLE schedules (id INTEGER PRIMARY KEY, workflow TEXT, cron TEXT, inputs TEXT,
  enabled INTEGER, last_run TEXT, next_run TEXT);
```

Single-writer discipline: `Db.write(fn)` acquires `.amber/amber.lock` (fcntl/msvcrt), opens a
connection with `isolation_level=None`, runs `BEGIN IMMEDIATE … COMMIT`. Readers open their
own connections. Never hold the lock across a network call.

## Concurrency model

- CLI commands are single-process. The scheduler is the only long-running process; it runs
  one program at a time per workflow.
- MCP clients are per-run; servers are started on run start and stopped at run end (stdio)
  or connected per run (HTTP).
- The proxy is a separate process per upstream server; fragments are appended with
  `O_APPEND` writes of ≤ 4 KB lines where possible (large results are stored as separate files
  `DIR/blobs/<sha>.json` and referenced).

## Security posture

- No network calls from `align`/`compile` except the model backend.
- Generated transform code runs through an AST allow-list and a restricted namespace; it is
  still Python, so `program.py` is treated like any code the user reviews before running
  live (REPORT.md says so).
- Live runs default to **dry-run for write-effect tools**. `--allow-writes` is explicit.
- Secrets only from env; `amber.toml` must not contain tokens (lint rule in `amber init`
  validation: reject values that look like keys).
