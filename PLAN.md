# PLAN.md — Amber v0.1 build plan

> Execute phases in order unless a phase is marked parallel-safe. Each phase lists the spec
> documents to read first, the tasks, and the **acceptance checks** that must pass before the
> phase is complete. Record outputs in `PROGRESS.md`. Defaults for open decisions are in
> `docs/11-decisions.md`; only items marked **ASK** require the user.

## 0. What we are building (one screen)

```
 record ──► align ──► compile ──► verify ──► run ──► doctor ──► fallback ──► (recompile)
   │          │          │           │         │        │           │
 traces    skeleton   program     replay    live     drift      agent run
 (JSON)    (JSON)     v1 (.py)    tests     runs     report     → new trace
```

- **record**: capture an agent run as a canonical trace. Adapters: `claude -p` stream-json
  wrapper, Claude Code transcript import, Claude Agent SDK, MCP stdio proxy (harness-agnostic).
- **align**: merge 1..N traces of the same workflow into a *skeleton*: ordered steps, each
  argument annotated with provenance (input / reference to earlier result / constant /
  template / unknown), each step classified `deterministic` | `transform` | `judgment`, dead
  exploratory steps pruned.
- **compile**: deterministic codegen from the skeleton → `program.py` + `manifest.json` +
  `tests/test_replay.py`; typed holes (transforms, judgment prompts/schemas) filled by the
  model and verified against recordings; bounded repair loop.
- **verify**: replay the program against each source trace with a *cassette* backend; every
  tool call must match the recording (name + canonical args) in order; `llm()` steps are
  served from recordings. 3/3 or it is not a program.
- **run**: execute live against real MCP servers (and a safe builtin subset of Claude Code
  tools) with guards; `llm()` uses a small model with schema-validated output; everything is
  written to a ledger.
- **doctor**: compare live tool schemas and recent run results with what was compiled; emit a
  diagnosis and a recommendation (`none` | `recompile` | `fallback` | `re-record`).
- **fallback**: on guard failure, propose → veto → arbitrate → hand the task back to the
  agent with resume context, record that run as a new trace, and `compile --refresh`.

Success for v0.1 = the "Definition of done" in `CLAUDE.md`.

## 1. Phase map

| Phase | Name | Depends on | Spec docs | Week |
|---|---|---|---|---|
| P0 | Bootstrap | — | `01-architecture.md`, `11-decisions.md` | 1 |
| P1 | Trace model + store | P0 | `02-trace-format.md` | 1 |
| P2 | Recorders (adapters) | P1 | `02-trace-format.md`, `03-cli-spec.md` | 1 |
| P3 | Align | P1 | `04-compiler.md` §A | 1–2 |
| P4 | Compile (codegen + holes + synthesizer) | P3 | `04-compiler.md` §B–D | 2 |
| P5 | Verify (cassette replay) | P4 | `04-compiler.md` §E, `05-runtime.md` §1 | 2 |
| P6 | Run (live backends, guards, ledger) | P5 | `05-runtime.md` | 3 |
| P7 | Doctor + fallback loop | P6 | `05-runtime.md` §4–6 | 3 |
| P8 | Examples (3 synthetic workflows) | P2 (servers), P6 (full) | `06-examples.md` | 3 |
| P9 | Web panel | P6 | `07-panel.md` | 4 |
| P10 | Bench, README, GIF, release | P7, P8, P9 | `09-launch.md` | 4 |

Parallel work: P8's mock servers and data generators only depend on P0 and can be built
in a subagent while P1–P3 proceed; P9 can be built in a subagent once P6's ledger schema is
frozen; docs/user-guide pages can be drafted alongside any phase. Everything on the critical
path P1 → P3 → P4 → P5 → P6 → P7 must be done sequentially by one agent because the models
are shared.

---

## P0 — Bootstrap

**Read:** `docs/01-architecture.md`, `docs/11-decisions.md`.

**Tasks**
- [x] Confirm the final name (`docs/11-decisions.md` D1). Default: project **Amber**,
      distribution `amber-compiler`, import package `amber`, CLI `amber`. If the user picks a
      different name, rename once now (there is no other rename step).
- [x] `uv init --package` (src layout), then set `[build-system]` to `hatchling` in
      `pyproject.toml` (distribution name `amber-compiler`, package dir `src/amber`), Python
      `>=3.12`, dependencies and extras from `CLAUDE.md`. Add `amber = "amber.cli.app:main"`
      console script.
- [x] `LICENSE` Apache-2.0 (D2), `README.md` stub (title, one paragraph, "under construction"),
      `CHANGELOG.md` (Keep a Changelog), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor
      Covenant), `SECURITY.md`.
- [x] `.gitignore` (Python, `.amber/`, `*.env`, `examples/*/.amber/`, `.venv`), `.editorconfig`.
- [x] `ruff` config (line length 100, rules: E,F,I,B,UP,SIM,RUF), `pyright` config
      (`typeCheckingMode = "standard"`, `strict = ["src/amber/trace", "src/amber/align", "src/amber/compile"]`).
- [x] `pytest` config: markers `live` (needs model access), `slow`; `asyncio_mode = "auto"`.
- [x] `.pre-commit-config.yaml`: ruff, ruff-format, pyright (local hook), gitleaks.
- [x] `.github/workflows/ci.yml`: matrix 3.12/3.13 on ubuntu + macos; steps: `uv sync
      --all-extras`, ruff, pyright, `pytest -q -m "not live"` with coverage upload as artifact.
- [x] `.github/workflows/release.yml`: on tag `v*` build sdist+wheel with `uv build` and
      publish to PyPI with Trusted Publishing (OIDC). Do not run it yet.
- [x] `Makefile` targets: `setup`, `test`, `lint`, `demo`, `drift`, `bench`, `ui`.
- [x] `src/amber/cli/app.py`: typer app with `--version`, `--json` global option plumbing,
      `init` command creating `.amber/` + `amber.toml` (D5: resolve model IDs via
      `anthropic.models.list()` when an API key is present; else write the documented
      defaults and a comment).
- [x] `PROGRESS.md` created with the P0 entry.

**Acceptance**
```bash
uv sync --all-extras && uv run amber --version        # prints amber 0.1.0a0
uv run amber init && test -f amber.toml && test -d .amber
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -q                                      # 1 smoke test passes
```

---

## P1 — Trace model + store

**Read:** `docs/02-trace-format.md` (all), `docs/01-architecture.md` §Storage.

**Tasks**
- [x] `src/amber/trace/models.py`: `Trace`, `ToolSchema`, `ToolCall`, `Thought`, `Note`,
      `Outcome`, `Usage` exactly as specified (Pydantic, `extra="forbid"`, `schema_version = 1`).
- [x] `src/amber/trace/canonical.py`: `canonical_tool_name(server, tool)`,
      `canonicalize_args(args, volatile_paths)`, `args_fingerprint(args)` (sha256 of canonical
      JSON), `normalize_result(result)` (MCP content blocks → JSON | text), JSON-path utilities
      (`iter_leaves`, `get_path`, `set_path`). Property tests with `hypothesis`: canonical form
      is idempotent and order-insensitive for dict keys.
- [x] `src/amber/trace/store.py`: `TraceStore(root=".amber")` with `save(trace)`,
      `load(trace_id)`, `list(workflow=None)`, `delete(trace_id)`, `latest(workflow)`. Traces as
      `.amber/traces/<workflow>/<trace_id>.json`; index table `traces` in `.amber/amber.db`
      (SQLite WAL, `PRAGMA journal_mode=WAL`, single writer helper `Db.write()` with a
      process-level lock file).
- [x] Tool schema fingerprint: `ToolSchema.hash = sha256(canonical(input_schema))[:16]`.
- [x] CLI: `amber traces ls [workflow] [--json]`, `amber traces show <id> [--json]`,
      `amber traces rm <id>`.
- [x] Fixtures: `tests/fixtures/traces/weekly-report/{t1,t2,t3}.json` hand-written minimal
      traces (3 steps, one judgment) used by P3–P5 tests.

**Acceptance**
```bash
uv run pytest -q tests/unit/trace                      # all pass, incl. hypothesis tests
uv run amber traces ls --json | python -c "import json,sys; json.load(sys.stdin)"
```

---

## P2 — Recorders (adapters)

**Read:** `docs/02-trace-format.md` §Adapters, `docs/03-cli-spec.md` (`record`, `import`, `proxy`).

**Tasks**
- [x] `adapters/base.py`: `Adapter` protocol (`name`, `async def record(...) -> Trace`).
- [x] `adapters/claude_messages.py`: shared mapper from Claude message dicts
      (`{"type":"assistant"|"user"|"system"|"result", ...}`) to trace events. Handles:
      `tool_use` → pending call; `tool_result` (string or content-block list, `is_error`) →
      closes the call by `tool_use_id`; `text` blocks → `Thought`; `result` → `Outcome` with
      usage; `mcp__<server>__<tool>` → `server.tool`; builtin names → `builtin.<Name>`;
      `parent_tool_use_id` → `parent_call_id`; unknown event types ignored with a debug log.
      Strip ANSI escape sequences from lines before `json.loads`.
- [x] `adapters/claude_stream.py`: `amber record <workflow> [--input k=v]... [--mcp cfg.json]
      [--allow PATTERN]... -- claude -p "<task>" [extra claude flags]`. Injects
      `--output-format stream-json --verbose` (and `--mcp-config`, `--allowedTools`,
      `--permission-mode` when the Amber flags are given) unless already present; refuses if
      the user passed a conflicting `--output-format`. Streams stdout line by line, prints a
      live step counter, writes the trace at exit, prints the summary table (calls, judgment
      candidates, tokens, cost, duration, trace id). Non-zero exit of `claude` → trace saved
      with `outcome.status = "failure"` and the CLI exits 1.
- [x] `adapters/claude_transcript.py`: `amber import <workflow> <session.jsonl | --latest>`.
      Implements the JSONL rules in `02-trace-format.md` §A2 (chain by `parentUuid`, filter to
      the file's own `sessionId`, skip `isSidechain`, handle `compact_boundary`, `toolUseResult`
      ignored, usage summed from assistant messages). `--latest` resolves
      `~/.claude/projects/<cwd with "/"→"-">/` newest `*.jsonl` that is not `agent-*.jsonl`.
- [x] `adapters/agent_sdk.py` (extra `[sdk]`): `amber record ... --sdk` runs
      `claude_agent_sdk.query(prompt, ClaudeAgentOptions(...))` and feeds messages to the same
      mapper (map `AssistantMessage/UserMessage/ResultMessage` dataclasses to dicts first).
- [x] `adapters/mcp_proxy.py`: `amber proxy --name <server> [--trace-dir DIR] -- <upstream cmd>`.
      A stdio MCP server built on the low-level `mcp.server.Server(on_list_tools=...,
      on_call_tool=...)` that connects to the upstream with `mcp.Client(StdioServerParameters(...))`,
      forwards `tools/list` verbatim (schemas untouched) and `tools/call`, and appends one JSON
      line per call to `DIR/<server>-<pid>.jsonl` (`ts, tool, args, result, is_error, duration_ms,
      input_schema_hash`). `amber import <workflow> --proxy DIR` folds fragments into a trace
      (ordered by `ts`; thoughts absent; `adapter = "mcp-proxy"`). Pass through `env` explicitly
      (mcp v2 stdio children get a minimal env allow-list).
- [x] Fixtures: `tests/fixtures/claude_stream/*.jsonl` (hand-made, abbreviated but
      shape-correct), `tests/fixtures/claude_transcript/session.jsonl` (with a compaction
      boundary and one sidechain line), `tests/fixtures/mcp_proxy/*.jsonl`.
- [x] Integration test (marked `live`): record `examples/weekly-report` once via
      `claude -p` using the mock server (requires P8 servers; if P8 not yet done, use a 5-line
      `tests/fixtures/servers/echo_server.py` built on `mcp.server.MCPServer`).

**Acceptance**
```bash
uv run pytest -q tests/unit/adapters
cat tests/fixtures/claude_stream/weekly_report_run1.jsonl | uv run amber record weekly-report --from-stdin --json \
  | python -c "import json,sys; t=json.load(sys.stdin); assert t['adapter']=='claude-stream' and len(t['events'])>0"
uv run amber import weekly-report tests/fixtures/claude_transcript/session.jsonl --json | python -c "import json,sys; t=json.load(sys.stdin); assert t['adapter']=='claude-transcript'"
# proxy round-trip against the echo server:
uv run python tests/integration/test_proxy_roundtrip.py      # prints OK and the fragment count
```

---

## P3 — Align

**Read:** `docs/04-compiler.md` §A (alignment, provenance, classification, pruning).

**Tasks**
- [x] `align/sequence.py`: Needleman–Wunsch global alignment of tool-call sequences; score =
      `+2` same canonical tool name, `+1` bonus if `args_fingerprint` equal, `-1` mismatch,
      `-1` gap. Multi-trace: progressive alignment against the longest trace as the anchor.
      Output: columns, each a list of `(trace_id, call_idx | None)`.
- [x] `align/provenance.py`: for every leaf value in every call's args find provenance in
      priority order: `Input(name)` (declared `--input`), `Ref(step, path)` (equal leaf in an
      earlier result of the same trace), `Template(parts)` (string containing ≥1 Input/Ref
      value, with ≥3 literal chars between/around; produce an f-string template), `Const`
      (identical across all traces, no other provenance), `Unknown`. Consistency rule: a
      provenance is accepted for a step argument only if the same kind resolves in every
      trace where the step is present (otherwise `Unknown`).
- [x] `align/classify.py`: step kinds: `deterministic` (all leaves Input/Ref/Template/Const),
      `judgment` (≥1 `Unknown` leaf that is a string ≥ 24 chars or an enum-like short string
      that varies across traces, or the preceding `Thought` contains decision words — see
      spec heuristics), `transform-needed` (≥1 `Unknown` numeric/structured leaf or an
      `Unknown` that looks derived: date arithmetic, aggregates, joins). Optional steps (present
      in some traces) become `conditional` with `predicate: Unknown` unless a data predicate is
      derivable (same step present iff an earlier result leaf satisfies `==`/`>`/non-empty).
- [x] `align/prune.py`: dead-step elimination — a read-only step (MCP `readOnlyHint` true,
      builtin `Read/Glob/Grep/WebFetch`, or name matching `get|list|search|read|fetch`) whose
      result has no downstream `Ref` and which is absent from ≥1 trace is pruned; keep a list
      `pruned_steps` for the report. `--keep-unreferenced` disables.
- [x] `align/skeleton.py`: `Skeleton` model (`workflow, traces, inputs (declared+inferred),
      steps[], pruned_steps[], volatile_paths{}, warnings[]`). Inferred inputs: `Unknown`
      leaves in args that vary across traces but are short scalars → promoted to inputs named
      from their arg key (`since`, `until`, `week`). `volatile_paths`: result leaves that
      differ across traces with no downstream Ref (timestamps, ids) → masked during verify.
- [x] CLI `amber align <workflow> [--show] [--json] [--keep-unreferenced]` writes
      `.amber/build/<workflow>/skeleton.json` and prints the step table (idx, tool, kind,
      provenance summary, present-in N/M).

**Acceptance**
```bash
uv run pytest -q tests/unit/align               # NW alignment, provenance, classify, prune cases from the spec tables
uv run amber align weekly-report --show         # on fixtures: 5 steps, 1 judgment, 1 transform-needed, 1 pruned
uv run amber align weekly-report --json | python -c "import json,sys; s=json.load(sys.stdin); assert s['steps'] and s['inputs']"
```

---

## P4 — Compile

**Read:** `docs/04-compiler.md` §B (program API), §C (codegen), §D (holes + synthesizer + repair loop).

**Tasks**
- [x] `runtime/program_api.py`: `Context` protocol used by generated code: `ctx.tool(name,
      **args) -> Any`, `ctx.llm(step_id, schema: type[BaseModel], prompt: str, context: dict)
      -> BaseModel`, `ctx.now() -> datetime`, `ctx.log(msg)`. `Program` metadata dataclass and
      the `@program(workflow=..., version=...)` decorator that registers the entry point and
      its `Inputs`/`Output` models for the runner and the panel.
- [x] `compile/manifest.py`: `Manifest` model (`workflow, version, created_at, source_traces[],
      skeleton_hash, tools[] (with schema hashes + effect), servers{}, inputs schema, output
      schema, llm_steps[] (id, schema, model hint), transforms[] (id, verified_on[]),
      volatile_paths, budgets (max_calls, max_llm_tokens), program_hash`).
- [x] `compile/codegen.py` + `templates/program.py.j2`, `templates/test_replay.py.j2`: emit
      `program.py` with `Inputs`, `Output`, one statement per skeleton step, `# AMBER-HOLE:<id>`
      stubs for transforms (`def transform_<id>(...)-> ...: raise NotImplementedError`) and
      judgment steps (`PROMPT_<id> = ...`, `class <Id>Out(BaseModel)`), conditional steps as
      `if predicate:`; emit `tests/test_replay.py` parametrized over source traces calling
      `amber.runtime.verify.replay(program, trace)`.
- [x] `compile/holes.py`: `Hole` kinds `transform` (signature from provenance: inputs are the
      referenced results/inputs, expected outputs are the recorded leaf values per trace) and
      `judgment` (expected outputs per trace = the Unknown arg values; available context =
      results referenced by the text + preceding `Thought`s). Hole verification: a transform fill
      is accepted only if `fn(recorded_inputs) == recorded_outputs` for every trace (with
      numeric tolerance 1e-9); a judgment fill is accepted if the schema validates against every
      recorded value and the prompt is ≤ 1200 chars and references only available context keys.
- [x] `models/backend.py`: `ModelBackend` protocol `async complete(schema: type[T], system:
      str, user: str, *, model: str, max_tokens: int) -> tuple[T, Usage]`;
      `models/anthropic_api.py` (`client.messages.parse(..., output_format=schema)`);
      `models/claude_cli.py` (`claude -p --output-format json` + JSON extraction + Pydantic
      validation + one retry; use the CLI's structured-output flag if `claude --help` shows
      one); `models/recorded.py` (fixtures for tests). Selection: `AMBER_MODEL_BACKEND` →
      `ANTHROPIC_API_KEY` present → `anthropic`; else `claude` on PATH → `cli`; else error with
      hint.
- [x] `compile/synthesizer.py`: fills holes with structured outputs (`TransformFill
      {python_source, rationale}`, `JudgmentFill {prompt, schema_fields[], rationale}`), runs
      hole verification, repair loop ≤ 3 iterations per hole feeding back the exact mismatch;
      on failure a transform hole is **downgraded to a judgment hole** (and a warning recorded);
      on judgment failure the compile fails with a clear message. Generated transform code is
      executed in a restricted namespace (no imports except `math, datetime, statistics,
      re, json, itertools, collections`; AST check rejects `import`, `exec`, `eval`, `open`,
      dunder access).
- [x] Program versions: `programs/<workflow>/v<N>/{program.py, manifest.json, tests/
      test_replay.py, REPORT.md}`; `amber programs ls/show/diff`.
- [x] CLI `amber compile <workflow> [--refresh] [--max-repair 3] [--model ID] [--json]`;
      `--refresh` re-aligns with all traces including new (fallback) ones and bumps the
      version.

**Acceptance**
```bash
uv run pytest -q tests/unit/compile             # codegen golden files, hole verification, sandbox AST rejects, synthesizer with recorded backend
AMBER_MODEL_BACKEND=recorded uv run amber compile weekly-report --json \
  | python -c "import json,sys; r=json.load(sys.stdin); assert r['version']==1 and r['holes_filled']>=2"
test -f programs/weekly-report/v1/program.py && uv run python -c "import ast,sys; ast.parse(open('programs/weekly-report/v1/program.py').read())"
```

---

## P5 — Verify (cassette replay)

**Read:** `docs/04-compiler.md` §E, `docs/05-runtime.md` §1–2.

**Tasks**
- [x] `runtime/backends/cassette.py`: `CassetteBackend(trace, volatile_paths, strict_order=True)`
      — queue of expected calls; `tool()` canonicalizes the request, masks volatile paths,
      compares with the head of the queue (or any pending call when `--unordered`), returns the
      recorded result; raises `ReplayDivergence(expected, actual, step_idx)` with a unified diff
      of canonical JSON. `llm()` → `RecordedLLMBackend` returns the recorded Unknown values for
      that step validated against the schema (or, with `--live-llm`, calls the real backend and
      records the comparison).
- [x] `runtime/verify.py`: `replay(program_path, trace) -> ReplayReport` (status, consumed
      calls, leftover calls, divergence, output equality).
- [x] CLI `amber verify <workflow> [--version N] [--trace ID] [--live-llm] [--unordered]
      [--json]` → table per trace; exit 3 on any failure. Also runnable as plain pytest via the
      generated `tests/test_replay.py`.
- [x] Hook verify into compile: compile marks the version `usable: true` only if verify passes
      for all source traces (`REPORT.md` includes the per-trace table).

**Acceptance**
```bash
uv run pytest -q tests/unit/runtime/test_cassette.py       # divergence cases: wrong tool, wrong arg, extra call, missing call, volatile masked
AMBER_MODEL_BACKEND=recorded uv run amber compile weekly-report && uv run amber verify weekly-report   # 3/3 PASS, exit 0
uv run amber verify weekly-report --trace t_broken; echo $?                                             # prints divergence, exit 3
```

---

## P6 — Run (live)

**Read:** `docs/05-runtime.md` §2–3 (live backends, builtins, guards, ledger, scheduler).

**Tasks**
- [x] `runtime/backends/live.py`: `LiveBackend(manifest, servers_config)` — opens one
      `mcp.Client` per server from `amber.toml [servers]` (stdio or streamable HTTP), caches
      `list_tools`, executes `call_tool`, normalizes results (`structured_content` → JSON; text
      that parses as JSON → JSON; else text), measures duration.
- [x] `runtime/backends/builtins.py`: safe subset of Claude Code tools for compiled programs:
      `builtin.Read`, `builtin.Write`, `builtin.Glob`, `builtin.Grep`, `builtin.WebFetch`
      (httpx GET, 10 MB cap, 30 s timeout), `builtin.Bash` under the policy: only the recorded
      command template with substituted provenance values; cwd pinned to the program's
      `workdir`; 120 s timeout; deny-list for destructive patterns (`rm -rf`, `sudo`, `curl |
      sh`, `> /dev/`). Any other builtin (`Edit`, `Task`, `WebSearch`, `NotebookEdit`) →
      compile-time error with hint ("re-record with MCP tools or mark the step as
      judgment").
- [x] `runtime/guards.py`: `ToolAllowlistGuard`, `ResultShapeGuard` (genson schema inferred
      from recordings per step; mismatch severity: missing required key = `hard`, extra key =
      `soft`), `BudgetGuard` (max_calls = 2× recorded max, max_llm_tokens from manifest,
      wall-clock), `WriteEffectGuard` (tool `effect == "write"` requires `--allow-writes`;
      otherwise the call is logged as `dry-run` and a placeholder result shaped like the
      recorded one is returned), `LLMOutputGuard` (schema + optional `confidence` ≥ threshold).
      Guards raise `GuardStop(kind, severity, detail)`.
- [x] `runtime/ledger.py`: SQLite tables `runs`, `run_steps`, `guard_events`, `llm_calls`,
      `decision_records` (single writer; schema in spec). Token/cost accounting per run.
- [x] `runtime/runner.py`: `run(program_version, inputs, mode=live, allow_writes, fallback)`
      → `RunResult`; prints a compact step log and a summary (steps, llm calls, tokens, cost,
      duration, status).
- [x] CLI `amber run <workflow> [--input k=v]... [--version N] [--dry-run] [--allow-writes]
      [--fallback agent|none] [--json]`; exit 4 when a guard stopped the run.
- [x] `runtime/scheduler.py` (P2 priority — do last in this phase): `amber schedule add
      <workflow> --cron "0 9 * * 1" --input k=v`, `amber schedule ls/rm`, `amber scheduler`
      (foreground loop using `croniter`; documented alternative: system cron / GitHub Actions
      calling `amber run`).

**Acceptance**
```bash
uv run pytest -q tests/unit/runtime                               # guards, builtins policy, ledger, live backend with the echo server
uv run amber run weekly-report --input week=2026-W44 --dry-run    # mock servers from P8; 0 agent turns; write steps logged as dry-run
uv run amber run weekly-report --input week=2026-W44 --allow-writes --json | python -c "import json,sys; r=json.load(sys.stdin); assert r['status']=='ok' and r['agent_turns']==0 and r['llm_calls']<=2"
```

---

## P7 — Doctor + fallback loop

**Read:** `docs/05-runtime.md` §4 (doctor), §5 (fallback policy), §6 (recompile).

**Tasks**
- [ ] `doctor/checks.py`: `SchemaDriftCheck` (live `list_tools` input-schema hash vs manifest
      per tool), `ResultShapeDriftCheck` (last N runs' soft/hard shape events), `GuardStatsCheck`
      (stop rate), `LLMHealthCheck` (validation failures, low confidence rate), `StalenessCheck`
      (traces newer than the program), `ServerReachabilityCheck`.
- [ ] `doctor/diagnosis.py`: `Diagnosis {status: healthy|degraded|broken, findings[],
      recommendation: none|recompile|fallback|re-record, rationale}` with the decision table
      from the spec. CLI `amber doctor <workflow> [--json]`; exit 5 when not healthy.
- [ ] `fallback/proposal.py`: `FallbackProposal` built from a `GuardStop` (step, kind, context
      summary, completed write steps).
- [ ] `fallback/sentry.py`: veto rules — `--no-fallback`; daily fallback budget (default 3 per
      workflow); a write step already executed in this run → no automatic agent re-run of the
      *whole* task (only `resume` mode); budget exhausted; server unreachable (fallback would
      fail too → recommend re-record).
- [ ] `fallback/arbiter.py`: decision table → `retry` (transient tool error, once),
      `fallback_resume` (drift at step k: agent continues from k with completed results as
      context), `abort`. Every decision → `decision_records` (proposal, vetoes, decision,
      reason, actor, ts).
- [ ] `fallback/agent_fallback.py`: builds the resume prompt (original task + inputs +
      completed steps' results as JSON + "the step `<tool>` failed with `<reason>`; continue
      from there"), runs the `claude-stream` adapter with the workflow's MCP config, stores the
      trace with `origin: fallback`, `resumed_from_step: k`. `amber compile --refresh` then
      includes it; the skeleton merges the resumed trace by aligning its calls after step k.
- [ ] Wire into runner: `--fallback agent` enables the loop; print the decision chain.

**Acceptance**
```bash
uv run pytest -q tests/unit/doctor tests/unit/fallback            # decision tables, veto rules, resume prompt golden file
cd examples/price-watch && make drift && uv run amber run price-watch --input product=all; echo $?      # exit 4 (guard)
uv run amber doctor price-watch --json | python -c "import json,sys; d=json.load(sys.stdin); assert d['status']!='healthy' and d['recommendation'] in ('fallback','recompile')"
uv run amber run price-watch --input product=all --fallback agent --allow-writes      # records a fallback trace (live)
uv run amber compile price-watch --refresh && uv run amber verify price-watch         # v2 verifies against all traces incl. fallback
```

---

## P8 — Examples (synthetic)

**Read:** `docs/06-examples.md`. Mock servers may be built early in a parallel subagent.

**Tasks**
- [x] `examples/_shared/`: `synth.py` (seeded Faker-free generators: names from word lists,
      deterministic ids), `outbox.py` (Slack-mock outbox writer), `mcp_common.py`.
- [ ] `examples/issue-triage/`: `server_github.py` (tools: `list_issues(state, since)`,
      `get_issue(number)`, `add_labels(number, labels)` [write], `search_issues(query)`),
      `server_slack.py` (`post_message(channel, text)` [write], `list_channels()`), `scenario.py`
      (seed → 40 issues across areas; scenario B adds a new label taxonomy), `task.md`,
      `mcp.json`, `Makefile`.
- [x] `examples/weekly-report/`: `server_shop.py` (SQLite with synthetic orders; tools:
      `week_range(week)`, `list_orders(since, until)`, `list_customers()`, `top_products(since,
      until, n)`), `server_slack.py` (shared), `scenario.py` (seed, 12 weeks of orders; scenario
      B renames `total` → `amount` to cause result-shape drift), `task.md`, `mcp.json`,
      `Makefile`.
- [ ] `examples/price-watch/`: `site/` static HTML catalog (6 fictional products) served by
      `python -m http.server` via Makefile; `mcp.json` uses `@playwright/mcp` (`npx -y
      @playwright/mcp@latest`) — document the Node requirement; `scenario.py` writes scenario A
      or B (B changes prices and renames the price CSS class → drift); `task.md`, `Makefile`.
- [ ] Each example Makefile: `record` (3 runs with different inputs), `align`, `compile`,
      `verify`, `run`, `drift`, `demo` (all of the above), `clean`.
- [ ] Top-level `make demo` = `examples/weekly-report` demo; `make drift` = price-watch drift
      story.
- [x] `examples/README.md` with what each example demonstrates and expected outputs.

**Acceptance**
```bash
uv run pytest -q tests/unit/examples                       # servers: list_tools schemas stable (golden), generators deterministic by seed
cd examples/weekly-report && make demo                     # live: 3 records, compile, verify 3/3, run ok (0 agent turns)
cd examples/issue-triage && make demo
cd examples/price-watch && make demo && make drift && make heal    # heal = run --fallback agent → compile --refresh → verify
```

---

## P9 — Web panel

**Read:** `docs/07-panel.md`.

**Tasks**
- [ ] `ui/app.py` (FastAPI) + Jinja templates + vendored `htmx.min.js`; `amber ui [--port 8787]
      [--open]`. Read-only against `.amber/amber.db` and `programs/`.
- [ ] Pages: Programs (versions, source traces, steps by kind), Program detail (step table,
      manifest, REPORT), Runs (list with status/tokens/cost/duration, filter by workflow), Run
      detail (step timeline, guard events, llm calls), Doctor (latest diagnosis per workflow,
      button `Run doctor` → POST), Decisions (ledger).
- [ ] Charts: server-rendered inline SVG: tokens per run (agent baseline vs compiled), guard
      events over time. Palette and mark rules from the spec (bars ≤ 24 px, 4 px radius on the
      data end only, hairline grid, legend when ≥ 2 series, table twin under each chart).
- [ ] Dark/light via `prefers-color-scheme`; no external CDN requests.

**Acceptance**
```bash
uv run pytest -q tests/unit/ui                             # routes 200, templates render with fixtures DB
uv run amber ui --port 8787 & sleep 2 && curl -s localhost:8787/ | grep -q "Programs" && kill %1
```

---

## P10 — Bench, README, GIF, release

**Read:** `docs/09-launch.md`, `docs/10-research.md`.

**Tasks**
- [ ] `bench/run_bench.py`: for each example, N agent runs (tokens from `result.usage`: input +
      cache_read + cache_creation, output; cost; wall time) vs N compiled runs (llm tokens,
      wall time, identical-tool-call rate vs recordings) → `bench/results.json`,
      `bench/results.md` (median, min, max, model IDs, date, commit).
- [ ] `README.md` per the skeleton in `09-launch.md`: hero line, GIF, install, 6-command
      story, how it works (mermaid), measured table, what it can't do yet, related work, FAQ,
      contributing, license.
- [ ] `scripts/demo_gif.sh`: `vhs` tape (or `asciinema` + `agg`) recording `make demo`
      trimmed to ≤ 60 s; output `docs/assets/demo.gif` ≤ 8 MB.
- [ ] Docs site pages (`docs/user/`): quickstart, concepts, CLI reference (generated from typer
      with `typer utils docs`), adapters, writing MCP servers Amber can compile, FAQ.
- [ ] `CHANGELOG.md` v0.1.0; version bump; tag `v0.1.0`; TestPyPI dry run then PyPI via
      `release.yml`; GitHub Release with the GIF and bench table.
- [ ] Post-release checks: `uvx --from amber-compiler amber --version` on a clean machine/container.

**Acceptance**
```bash
uv run python bench/run_bench.py --all --runs 3 && test -s bench/results.md
uv build && uv run twine check dist/*
uvx --from ./dist/amber_compiler-0.1.0-py3-none-any.whl amber --version
```

---

## 2. Risk register (keep updated in PROGRESS.md)

| Risk | Mitigation |
|---|---|
| `claude` CLI flags change (`--output-format`, `--mcp-config`, `--allowedTools`) | Detect via `claude --help` at startup; keep flag names in one module (`adapters/claude_cli_flags.py`) |
| mcp v2 API surface differs from spec snippets | Verify against installed version first thing in P2; adjust `docs/02-trace-format.md` |
| Structured outputs reject schemas (missing `additionalProperties: false`) | All models `extra="forbid"`; unit test that `model_json_schema()` has it at every object level |
| Alignment produces nonsense on highly exploratory Claude Code sessions | Pruning + `--show` transparency + judgment downgrade; document "record with MCP tools, keep tasks tight" |
| Transform holes unverifiable (floating point, ordering) | Tolerance, sorted comparisons for lists of dicts by stable keys, downgrade to judgment |
| Benchmarks look bad on tiny tasks | Report honestly; choose examples with ≥ 8 tool calls |
| Playwright MCP flakiness in CI | price-watch live tests are `live`-marked; CI runs unit only |
| Name collision (PyPI `amber` taken; `amber-lang` compiler exists) | Distribution `amber-compiler`; see D1 |

## 3. Out of scope for v0.1 (say no politely)

OpenAI Agents SDK / OpenTelemetry adapters (v0.2), TypeScript program output, parallel steps,
multi-agent traces (sidechains), hosted runner, approval UI for writes, Windows support
beyond "best effort", editing programs from the panel.
