---
title: "Concepts"
tags: [amber/user]
---
# Concepts

```
record ──► align ──► compile ──► verify ──► run ──► doctor ──► fallback ──► (recompile)
```

**Trace.** One recorded agent run in a harness-independent JSON shape: the tool calls (canonical
`server.tool` names, args, results), the assistant's visible thoughts between calls, the
declared `--input`s, the tool schemas and the outcome (status, tokens, cost). Adapters produce
traces; everything downstream only reads them. Stored under `.amber/traces/<workflow>/`.

**Skeleton.** What 1..N traces of the same workflow have in common: an ordered list of steps.
Alignment (Needleman-Wunsch on tool names) lines the runs up; provenance mining explains every
argument (`Input(week)`, `Ref(0,$.start)`, `Template("week="+Input)`, `Const(5)`, or `Unknown`);
classification labels each step `deterministic`, `transform-needed` or `judgment` (and
`conditional` when it only sometimes runs); pruning drops exploratory reads nobody uses.

**Holes.** The only places the model writes code or prompts. A *transform* hole is a Python
function that must reproduce the recorded values exactly (checked in a sandbox); a *judgment*
hole is a prompt plus a Pydantic schema that must validate every recorded value; a *gate* decides
whether a conditional step runs. Every fill is verified against the recordings; a transform the
model cannot write is downgraded to a judgment.

**Program.** `programs/<workflow>/v<N>/program.py`: `Inputs`, `Output`, the filled holes and one
`ctx.tool(...)` per step in skeleton order. `manifest.json` carries the tool schemas (hashes +
effects), result-shape schemas, budgets and the trace mapping; `tests/test_replay.py` replays the
source traces; `REPORT.md` explains the holes and the verify table.

**Verify.** A cassette backend serves the recorded results and insists that every call matches
the recording (name + canonical args, volatile paths masked). A program that fails replay is
not usable, and `amber run` refuses it.

**Guards.** Live runs enforce a tool allowlist, call/token/wall-clock budgets, result-shape
checks against the recordings, judgment-output validation and a write-effect policy (dry-run by
default; `--allow-writes` to execute). A hard guard stops the run (exit 4) and feeds the doctor.

**Ledger.** SQLite tables for runs, steps, guard events, llm calls, decision records and
schedules under `.amber/amber.db`. The panel (`amber ui`) and the doctor read it.

**Doctor.** Probes the servers (schema drift), inspects recent runs (result-shape and guard
statistics, llm health), compares traces with the program (staleness) and lints the program.
The verdict is `healthy`, `degraded` or `broken` with a recommendation: `none`, `recompile`,
`fallback` or `re-record`.

**Fallback.** `amber run --fallback agent`: a stopped run becomes a *proposal*; the *sentry*
applies veto rules (daily budget, writes already done, servers unreachable, …); the *arbiter*
decides `retry`, `fallback_resume` or `abort` and writes the decision to the ledger before
anything acts. On `fallback_resume` the agent is relaunched with the completed steps as context,
its run is recorded as a `fallback` trace, and `amber compile --refresh` folds it into a new
version that verifies against every trace.
