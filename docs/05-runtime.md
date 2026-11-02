---
title: "05 — Runtime: run, guards, ledger, doctor, fallback, recompile"
tags: [amber/spec, runtime]
phases: [P5, P6, P7]
status: spec
created: 2026-11-02
---
# 05 — Runtime: run, guards, ledger, doctor, fallback, recompile

## 1. Runner (`amber.runtime.runner`)

```
load program version (lint program.py with compile/lint.py; refuse if usable=false unless --force)
validate inputs against Inputs model (CLI --input k=v → coerced by Pydantic)
build backend:
    verify → CassetteBackend + RecordedLLMBackend
    run    → LiveBackend(manifest, servers) + Builtins + ModelBackend(judgment) + Guards
open ledger run (status=running)
execute run(inputs, ctx) inside a wall-clock timeout
on success: status=ok; persist Output; print summary
on GuardStop: status=stopped; persist; if --fallback agent → fallback pipeline (§5) ; exit 4
on exception: status=failed; persist traceback (truncated); exit 1
```

`Context` is a sync facade: the runner owns an event loop in a worker thread; `ctx.tool`
submits a coroutine and blocks on its future (programs stay simple synchronous code).

## 2. Live backends

### 2.1 `LiveBackend` (MCP)

- For each server in `manifest.servers` (names) look up `amber.toml [servers.<name>]`:
  stdio → `Client(StdioServerParameters(command, args, env))`; http → `Client(url)`.
- On start: `list_tools()` → cache `{name: Tool}`; compute `schema_hash` per tool; compare to
  manifest → `SchemaDrift` guard events (`soft` if only `description` changed, `hard` if
  `input_schema` changed or tool missing).
- `call(tool, args)`: `await client.call_tool(name, args)` → `normalize_result` (see
  `02-trace-format.md`). `is_error=True` → `ToolError` (retryable once by the arbiter if the
  error text matches transient patterns: timeout, 429, 5xx, connection reset).
- Effect detection: `Tool.annotations` (`readOnlyHint`, `destructiveHint`) → `read` /
  `write`; absent → manifest value from compile time (derived from name heuristics) with
  `unknown` treated as `write` for the write-effect guard (conservative).

### 2.2 Builtins (`amber.runtime.backends.builtins`)

| tool | implementation | policy |
|---|---|---|
| `builtin.Read` | read file under `workdir`, text, optional `offset/limit` | path must resolve inside `workdir` or an allowlisted dir from `amber.toml [builtins].read_roots` |
| `builtin.Write` | write file (effect `write`) | same path rule; dry-run unless `--allow-writes` |
| `builtin.Glob`, `builtin.Grep` | `pathlib` / `re` over `workdir` | same |
| `builtin.WebFetch` | `httpx.get(url, timeout=30, follow_redirects=True)`, ≤ 10 MB, returns text (HTML → text via a tiny tag stripper) | only `http(s)`; host allowlist optional |
| `builtin.Bash` | `subprocess.run(shlex.split(cmd), cwd=workdir, timeout=120, capture_output=True)` | `cmd` must equal the compiled template with provenance substitutions (the program cannot build arbitrary commands); deny-list regex (`\brm\s+-rf\b`, `\bsudo\b`, `curl[^|]*\|\s*(ba)?sh`, `>\s*/dev/`, `\bmkfs\b`, `\bdd\b`); pipes allowed only if present in the recording; env scrubbed to `PATH, HOME, LANG` + `[builtins].bash_env` |
| others (`Edit`, `Task`, `WebSearch`, `NotebookEdit`, `TodoWrite`, `LS`, ...) | — | compile-time error: "step k uses builtin.<Name>, which Amber cannot run; re-record with MCP tools or mark as judgment" |

Builtin tools in Claude Code transcripts have well-known input keys (`Bash.command`,
`Read.file_path`, `Write.file_path/content`, `Glob.pattern/path`, `Grep.pattern/path`,
`WebFetch.url/prompt`). `WebFetch.prompt` is dropped (Amber returns raw text; a judgment
step can process it).

### 2.3 Judgment backend (`ctx.llm`)

`ModelBackend.complete(schema, system=JUDGMENT_SYSTEM, user=render(prompt, context),
model=cfg.models.judgment, max_tokens=1200)`. `JUDGMENT_SYSTEM` = "You are one step of a
compiled workflow. Answer only with the requested fields. If the context is insufficient,
set confidence below 0.5 and explain in `notes` if present." Context rendered as compact JSON
(whole values up to a 40 KB total, then the largest value shrunk first; see the note in
`04-compiler.md` §D1). Usage recorded in `llm_calls`.

## 3. Guards (`amber.runtime.guards`)

Each guard implements `before_call(step, tool, args)` / `after_call(step, tool, args,
result)` / `after_llm(step, output)` and raises `GuardStop(kind, severity, detail)` or
records `GuardEvent(severity="soft")`.

| guard | triggers | severity |
|---|---|---|
| `ToolAllowlistGuard` | tool not in `manifest.tools` | hard |
| `SchemaDriftGuard` | live input-schema hash ≠ manifest | hard (missing tool / schema change), soft (description only) |
| `ResultShapeGuard` | result fails the genson-inferred schema for the step: missing required key → hard; type change → hard; extra keys → soft; empty list where recordings never had empty → soft | per rule |
| `BudgetGuard` | calls > `max_calls_factor × max recorded`; llm tokens > `max_llm_tokens`; wall-clock > `wall_clock_s` | hard |
| `WriteEffectGuard` | `effect == write` and not `--allow-writes` → **dry-run**: log the call, return a placeholder shaped like the recorded result (recorded result with volatile paths masked); never raises | info |
| `LLMOutputGuard` | schema validation failure (after one automatic retry) → hard; `confidence < 0.5` → soft (configurable `[guards].min_confidence`) | per rule |
| `ErrorGuard` | tool `is_error` after retry policy | hard |

Hard stops end the run (`status=stopped`, `stop_kind`, `stop_detail`) and feed the fallback
pipeline. Soft events are ledgered and feed doctor statistics.

Inferred result schemas: at compile time, `genson.SchemaBuilder` over all recorded results per
step; `required` = keys present in every recording; arrays: item schema from union. Stored in
`manifest.result_schemas[step]`.

## 4. Doctor (`amber.doctor`)

Checks (each returns `Finding(check, severity: ok|soft|hard, detail)`):

| check | method |
|---|---|
| `servers` | connect to every server in the manifest; `list_tools` |
| `schema_drift` | compare live tool hashes vs manifest (missing tool = hard) |
| `result_shape` | last `--runs N` (default 10) runs: hard shape events on any step → hard; soft events on ≥ 50 % runs → soft |
| `guard_stats` | stop rate over last N runs ≥ 30 % → soft; ≥ 60 % → hard |
| `llm_health` | validation failure rate ≥ 20 % → soft; low-confidence rate ≥ 50 % → soft |
| `staleness` | traces newer than the program version (e.g. fallback traces not yet compiled) → soft |
| `program_lint` | program.py passes lint; manifest hash matches file → hard if not |

Decision table:

| findings | status | recommendation |
|---|---|---|
| all ok | healthy | none |
| only `staleness` | healthy | recompile (`--refresh`) |
| any soft, no hard | degraded | recompile if `schema_drift` soft or shape soft; else none |
| hard `schema_drift` or hard `result_shape` with servers reachable | degraded | fallback (agent re-derives the changed step, then recompile) |
| `servers` hard (unreachable) | broken | re-record / fix config (fallback would fail too) |
| `program_lint` hard | broken | recompile |

Output: `Diagnosis` (JSON + table). Exit 5 unless healthy.

## 5. Fallback policy (proposal → sentry → arbiter → ledger)

Modeled on a production pattern the author uses: nothing acts without a proposal, a veto
pass, an arbiter decision and a single-writer ledger record.

```python
class FallbackProposal(BaseModel):
    run_id: str; workflow: str; version: int; step_idx: int; tool: str
    stop_kind: str; stop_detail: str; completed_steps: list[int]; completed_write_steps: list[int]
    proposed_action: Literal["retry", "fallback_resume", "fallback_full", "abort"]

class Veto(BaseModel): rule: str; reason: str

class Decision(BaseModel):
    action: Literal["retry", "fallback_resume", "abort"]; reason: str; actor: Literal["arbiter", "user"]
```

Sentry rules (all evaluated, any veto blocks the vetoed action, not necessarily all):

| rule | vetoes |
|---|---|
| `no_fallback_flag` | fallback_* when `--fallback none` |
| `daily_budget` | fallback_* when `decision_records` show ≥ `[fallback].daily_budget` fallbacks today for this workflow |
| `writes_already_done` | `fallback_full` when `completed_write_steps` non-empty (re-running from scratch would duplicate side effects); `fallback_resume` allowed |
| `servers_unreachable` | fallback_* when the stop kind is server/connection (recommend re-record) |
| `budget_exhausted` | retry when the stop was `BudgetGuard` |
| `lint_failure` | everything except abort |
| `writes_not_allowed` | fallback_* when the run was started without `--allow-writes` and the remaining steps include write-effect tools that the MCP config cannot exclude (the agent might perform the write) |

Arbiter table:

| stop kind | action |
|---|---|
| transient tool error (first time) | retry (once, same step) |
| schema drift / result shape hard / llm validation hard / error after retry | fallback_resume |
| budget, allowlist, lint | abort |

Every proposal, veto list and decision is written to `decision_records` by the single writer
before any action executes.

### 5.1 Agent fallback (`fallback/agent_fallback.py`)

Resume prompt (template `fallback/resume_prompt.md.j2`):

```
You are resuming an automated workflow "{{workflow}}" that was compiled from your earlier runs.
Inputs: {{inputs_json}}
Completed steps and their results (do not redo them):
{% for s in completed %}- {{s.idx}} {{s.tool}}({{s.args_preview}}) → {{s.result_preview}}
{% endfor %}
Step {{step_idx}} `{{tool}}` could not be completed: {{stop_detail}}
Continue from that step and finish the task: {{task_prompt}}
Use only the tools provided. When done, state the final result in one paragraph.
```

Executes the `claude-stream` adapter with the workflow's recorded MCP config and allow
patterns (stored in the manifest at record time), `--allow-writes` semantics passed through
(if writes were not allowed in the run, the agent is instructed not to perform write
actions and the MCP config excludes write tools where the server supports it — otherwise the
fallback is vetoed by `writes_not_allowed`). The resulting trace has `origin="fallback"`,
`resumed_from_step=k`, `inputs` = the run's inputs. The run record gets `agent_turns` from
the result event (this is the only place a run can have `agent_turns > 0`).

## 6. Recompile (`amber compile --refresh`)

- Align all successful traces for the workflow including fallback traces. A resumed trace is
  aligned against the anchor starting at column `k` (prefix columns `< k` are filled from the
  run's completed steps, which are stored with the trace as `prefix_calls`).
- New skeleton hash → version `N+1`; verify against **all** traces (full and resumed; for
  resumed traces the cassette starts at step `k` with the prefix results preloaded).
- REPORT.md lists what changed (steps added/removed/reclassified, holes refilled) and the
  `programs diff` command.

## 7. Scheduler (P2, keep small)

`croniter` loop every 30 s; for each enabled schedule with `next_run <= now`: run via the
runner with `--fallback` from the schedule (default `none`), update `last_run/next_run`,
write a log line. Document the simpler alternatives first in the user guide (cron, GitHub
Actions with `uv run amber run ...`).
