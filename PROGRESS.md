---
title: "PROGRESS — Amber build log"
tags: [amber/log]
created: 2026-11-02
---
# PROGRESS.md — Amber build log

> [!NOTE]
> One dated entry per phase (see [PLAN.md](PLAN.md)). Each entry records the decisions taken
> from [docs/11-decisions.md](docs/11-decisions.md), what was built, the exact acceptance
> commands and their key output lines. Boxes in `PLAN.md` are ticked only after the output is
> recorded here.

## 2026-11-02 — Repository setup (pre-P0)

- Imported the plan package (`CLAUDE.md`, `PLAN.md`, `docs/00..11`) and made the repository an
  Obsidian-compatible vault: YAML frontmatter on every spec, `docs/index.md` map of content,
  `.obsidian/app.json` (markdown links, relative paths), workspace files gitignored.
- Remote: https://github.com/enestanerr/Amber (branch `main`).
- Environment verified: uv 0.13.6 · Python 3.12.10 (uv-managed) · git 2.55 · gh 2.103 ·
  claude CLI 2.1.316 · node 22.20 · GNU make 4.4.1 · gitleaks 8.30.1.
- Host is Windows 11; the plan targets macOS/Linux with Windows "best effort". Acceptance
  commands are run through Git Bash. `.gitattributes` forces LF so goldens stay byte-identical.

## 2026-11-02 — P0 Bootstrap

**Decisions taken (docs/11-decisions.md)**

| id | decision |
|---|---|
| D1 | **Default taken, not asked** (the user asked to start building autonomously and the GitHub repository is already named *Amber*): project **Amber**, distribution `amber-compiler`, import `amber`, CLI `amber`. The collision facts (PyPI `amber` placeholder, `amber-lang/amber`) stand; renaming later means touching `pyproject.toml`, `src/amber`, README and the console script. |
| D2 | Apache-2.0 (`LICENSE`, `license = "Apache-2.0"` in `pyproject.toml`). |
| D5 | `amber init` resolves model IDs via `anthropic.models.list()` when `ANTHROPIC_API_KEY` is set; otherwise writes `claude-sonnet-5-5` / `claude-haiku-5-5` with a `# verify` comment. Never hardcoded outside `amber.toml` except as the documented defaults in `amber/models/ids.py`. |
| D7 | SQLite + JSON files (store lands in P1). |
| D12 | Python ≥ 3.12; CI matrix 3.12 and 3.13 on ubuntu + macos. |
| D16 | Code, docs and README in English. |

**Spec corrections**

- `mcp` 2.3.0 has no `__version__`; the version-check command in `CLAUDE.md` and
  `docs/README-FIRST.md` now uses `importlib.metadata.version`.
- Installed API surfaces confirmed for P2: `mcp` 2.3.0 exports `Client`,
  `StdioServerParameters`; `mcp.server` exports `Server`, `MCPServer`, `ServerRequestContext`.
  `anthropic` 2.3.0. `claude --help` (2.1.316) shows `--output-format stream-json`,
  `--mcp-config`, `--strict-mcp-config`, `--permission-mode`, `--allowedTools`, `--verbose`
  and `--json-schema` (structured output for `-p`, usable by the `cli` model backend in P4).
- `ruff format` 0.17 formats fenced Python in Markdown; `*.md` and `docs/` are excluded so the
  specs keep their compact pseudo-code.

**Built**

- `pyproject.toml` (hatchling, `src/` layout, extras `sdk`/`ui`/`dev`, ruff/pyright/pytest/
  coverage config), `uv.lock`, `LICENSE`, `README.md` stub, `CHANGELOG.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.editorconfig`, `.gitattributes`, `Makefile`.
- `.pre-commit-config.yaml` (ruff, ruff-format, pyright local hook, gitleaks),
  `.github/workflows/ci.yml` (matrix, ruff, pyright, pytest `-m "not live"` with coverage
  artifact, gitleaks job), `.github/workflows/release.yml` (tag `v*` → `uv build` → Trusted
  Publishing; not run yet).
- `src/amber/`: `version.py`, `errors.py` (exit-code hierarchy), `config.py` (`amber.toml`
  model, `extra="forbid"`, secret lint), `project.py` (`init_project`), `util/{json,hashing,
  time,logging,paths}.py`, `models/ids.py`, `cli/{app,_output,init_cmd}.py`.
- `tests/conftest.py` (live tests skipped unless `-m live`; env cleared) and
  `tests/unit/test_smoke.py` (7 tests).

**Acceptance (run 2026-11-02, Windows 11 / Git Bash)**

```
$ uv sync --all-extras && uv run amber --version
amber 0.1.0a0

$ uv run amber init && test -f amber.toml && test -d .amber        # in a scratch directory
root     <scratch>/p0-init
created  .amber, amber.toml, .gitignore
models   synthesis=claude-sonnet-5-5 judgment=claude-haiku-5-5 (defaults)
ACCEPT: amber.toml and .amber exist

$ uv run ruff check . && uv run ruff format --check . && uv run pyright
All checks passed!
22 files already formatted
0 errors, 0 warnings, 0 informations

$ uv run pytest -q
7 passed in 0.32s
```

**Deferred / notes**

- `amber init` was run in a scratch directory, not at the repository root: the root is the
  Amber source tree, each `examples/<name>/` becomes its own project root in P8.
- `release.yml` needs the PyPI Trusted Publisher configured before the first tag.

## 2026-11-02 — P1 Trace model + store

**Decisions**: D7 (SQLite + JSON files; files are the source of truth, SQLite is an index that
`TraceStore.sync()` reconciles so hand-copied traces are picked up).

**Placement note (harness-agnostic rule)**: `trace/canonical.py` only knows the generic
`server.tool` form and a builtin-effect table keyed by bare tool names. Parsing Claude's
`mcp__<server>__<tool>` names lives in `adapters/claude_messages.py` (P2), so nothing under
`trace/` mentions Claude.

**Built**

- `trace/models.py`: `Usage`, `ToolSchema` (canonical + hash auto-filled, hash checked),
  `ToolCall`, `Thought`, `Note`, `Outcome`, `Trace` (invariants in `check()`, also run on
  validation). All `extra="forbid"`.
- `trace/jsonpath.py`: `$.a.b`, `$.a[0]`, `$.a[*]`, `["k.ey"]`; `parse_path`, `format_path`,
  `iter_leaves`, `get_path` (`MISSING` sentinel), `expand_wildcards`, `set_path`.
- `trace/canonical.py`: `canonical_tool_name`, `split_canonical`, `builtin_effect`,
  `canonical_json`, `canonicalize_args` (trailing-whitespace strip + `<volatile>` masking),
  `args_fingerprint`, `normalize_result` (string / block list / `structuredContent` /
  attribute objects / blobs → `{"$blob": …}` with an optional sink).
- `util/db.py`: `Db` (WAL, `busy_timeout`, `write(fn)` under a `fcntl`/`msvcrt` lock file,
  `BEGIN IMMEDIATE … COMMIT`, rollback on error). `trace/store.py`: `TraceStore`
  (`save/load/list/latest/delete/sync/load_all`, `TraceSummary` = index row).
- CLI: `amber traces ls [workflow] [--json]`, `amber traces show <id> [--json|--full]`,
  `amber traces rm <id>`.
- Fixtures: `tests/fixtures/gen_weekly_report.py` (seeded, fictional Northwind Lanterns Co.)
  writes `tests/fixtures/traces/weekly-report/{t1,t2,t3,t_broken}.json`. t2 has one
  exploratory `builtin.Read` (pruned in P3); `shop.rfm_snapshot(as_of=<day before start>)` is
  the transform-needed step; `slack.post_message.text` is the judgment; `t_broken` swaps
  steps 2/3 and adds `shop.list_customers`.
- Tests: 52 in `tests/unit/trace` (hypothesis: canonical JSON idempotent and key-order
  insensitive; `canonicalize_args` idempotent), 5 in `tests/unit/cli/test_traces.py`.

**Acceptance (2026-11-02)**

```
$ uv run pytest -q tests/unit/trace
52 passed in 1.32s

$ uv run amber traces ls --json | python -c "import json,sys; json.load(sys.stdin)"   # scratch project with t1..t3 copied in
ACCEPT: json ok, 3 traces

$ uv run amber traces show t2
t2 · weekly-report · claude-stream  … 8 rows (2 thoughts, 6 calls incl. builtin.Read)
outcome: success · calls 6 · tokens in 18,122 (cached 12,000) · out 966 · $0.034 · 19.5 s
```

Coverage (`--cov=amber.trace`): canonical 98 % · jsonpath 91 % · models 94 % · store 98 %.

## 2026-11-02 — P2 Recorders (adapters)

**Decisions**: D6 order (claude-stream → claude-transcript → mcp-proxy → agent-sdk). D14:
stream traces are `success` only when `result.subtype == "success"` and `is_error` is false;
no `result` event or a non-zero exit → `failure` (trace still saved, CLI exits 1).

**Spec corrections / notes** (recorded in `docs/02-trace-format.md`)

- `mcp` 2.3.0 Python API is snake_case (`Tool.input_schema`, `ToolAnnotations.read_only_hint`).
- Stream-json `init` lists tool names only → stub schemas; `amber record --mcp` enriches them
  from the servers after the run (`adapters/enrich.py`). The proxy writes real schemas.
- `amber record --from-stdin` gained `--prompt`; `mcp__…` parsing lives in the adapter.
- `claude --version` is captured into `harness.version` for live recordings.

**Built**

- `adapters/base.py` (`RecordRequest`, `Adapter` protocol), `adapters/claude_cli_flags.py`
  (single place for Claude flag names; argv preparation with variadic-flag awareness),
  `adapters/claude_messages.py` (shared mapper: init/assistant/user/result, ANSI strip,
  `permission_request` abort, denial detection, unclosed-call warnings),
  `adapters/claude_stream.py` (subprocess streaming with a 16 MB line limit, stderr tail,
  stdin replay), `adapters/claude_transcript.py` (session filter, sidechain/meta skip,
  `parentUuid` chain with longest-branch rule, compaction note, `--latest` path encoding
  incl. Windows drive letters), `adapters/mcp_proxy.py` (low-level `mcp.server.Server`
  proxy, fragment writer with 64 KB blob offload, importer ordered by `ts`),
  `adapters/agent_sdk.py` (duck-typed dataclass → dict conversion, lazy SDK import),
  `adapters/enrich.py`.
- CLI: `amber record`, `amber import`, `amber proxy`; `cli/_inputs.py` for `--input k=v`.
- Fixtures regenerated from one seeded generator: `claude_stream/weekly_report_run{1,2,3}.jsonl`
  (with `stream_event`, `rate_limit_event` and an ANSI-prefixed line), the trace goldens
  `t1..t3` are now literally the mapper's output + schema enrichment (`started_at`/`duration_ms`
  are `None` for replayed streams), `claude_transcript/session.jsonl` (compaction, sidechain,
  foreign session, 4 calls), `mcp_proxy/{shop-1234,slack-1235}.jsonl`,
  `servers/echo_server.py` (`MCPServer`).
- Tests: 36 in `tests/unit/adapters` (incl. a fake `claude` shim on PATH exercising the real
  subprocess path), 6 CLI tests, `tests/integration/test_proxy_roundtrip.py` (offline, real
  stdio proxy in front of the echo server), `tests/integration/test_record_live.py` (`live`).

**Acceptance (2026-11-02)**

```
$ uv run pytest -q tests/unit/adapters
36 passed

$ cat tests/fixtures/claude_stream/weekly_report_run1.jsonl | uv run amber record weekly-report --from-stdin --json | python -c "..."
ACCEPT record: t_20261102_165647_4b9d4e 7 events        # adapter == claude-stream

$ uv run amber import weekly-report tests/fixtures/claude_transcript/session.jsonl --json | python -c "..."
ACCEPT import: t_20261102_165648_0f4ef2                 # adapter == claude-transcript

$ uv run python tests/integration/test_proxy_roundtrip.py
OK 3 fragments

$ uv run amber record weekly-report --from-stdin --input week=2026-W43 < run2.jsonl
Recorded weekly-report → t_20261102_165648_bc39fe  (success)
  tool calls      6   (builtin 1 · shop 4 · slack 1)
  judgment cands  1   (slack.post_message.text)
  tokens          in 18,122 (cached 12,000) · out 966 · $0.034 · 19.5 s
```

Full suite: 111 passed, 1 skipped (live). ruff + pyright clean.

## 2026-11-02 — P3 Align

**Built** (`src/amber/align/`, pyright strict)

- `sequence.py`: Needleman-Wunsch (`+2` tool match, `+1` fingerprint bonus, `-1`
  mismatch/gap, diagonal-first ties, gap in the shorter sequence), progressive alignment
  against the longest trace. Two additions over the spec text: a diagonal pair of *different*
  tools is split into two columns (one mismatch scores better than two gaps, but a column must
  hold one tool), and adjacent single-trace columns of the same tool are merged with a
  `reordered_steps` warning (`A,B` vs `B,A` → two core columns, anchor order kept).
  `repeated_call` warning for ≥ 3 consecutive same-tool columns.
- `provenance.py`: Input → Ref (first column, shortest path) → Template (longest-first greedy
  decomposition over inputs and earlier string/number leaves; ≥ 3 literal chars; re-rendered
  and checked in every trace) → Const → Unknown (`large` ≥ 20 KB). Type-aware leaf equality
  (numbers numeric, strings exact modulo trailing whitespace). Consistency rule: 2/3 → Unknown.
- `classify.py`: per-Unknown-leaf hole kind (date/ISO → transform; ≥ 24 chars, enum-like with
  repetition, decision cue, large → judgment; numbers/bools/structured → transform); short
  free strings that vary are promoted to inferred inputs named from the arg key; optional steps
  get a data predicate (`non-empty`, `==`, `!=`, `>`, `<` on an earlier core result leaf) or an
  unknown gate.
- `prune.py`: fixpoint dead-step elimination (read-only ∧ unreferenced ∧ optional ∧ not last,
  plus all-error unreferenced calls); core unreferenced reads → `observe`; kept steps are
  renumbered and refs remapped.
- `skeleton.py` / `models.py`: `Skeleton`, `StepSpec` (adds `calls: trace_id → event idx` so
  later phases can fetch recorded values), `Provenance` (adds `hole`), `Predicate`,
  `InputSpec`; `context_hint` (referenced steps + steps whose result strings are echoed in the
  judgment text or whose tool name is mentioned in the thoughts); generalized volatile paths
  (`$[*].created_at`); stable 16-hex hash.
- CLI `amber align <wf> [--show] [--json] [--keep-unreferenced] [--include-failed]` →
  `.amber/build/<wf>/skeleton.json`. Failed traces are skipped by default; unknown-status
  imports are kept with a warning.

**Acceptance (2026-11-02)**

```
$ uv run pytest -q tests/unit/align
31 passed  (+5 CLI tests in tests/unit/cli/test_align.py)

$ uv run amber align weekly-report --show                 # scratch project with t1..t3
Skeleton weekly-report  (3 traces · 5 steps · 1 judgment · 1 transform-needed · 1 pruned)
1  shop.week_range     deterministic            week ← Input(week)                       3/3
2  shop.list_orders    deterministic · observe  since ← Ref(0,$.start) · until ← Ref(0,$.end)
3  shop.top_products   deterministic · observe  n ← Const(5) · since ← Ref(0,$.start) · until ← Ref(0,$.end)
4  shop.rfm_snapshot   transform-needed · observe  as_of ← Unknown(transform)            3/3
5  slack.post_message  judgment                 channel ← Const('#eng') · text ← Unknown(judgment)
-  builtin.Read (pruned)                        read-only, unreferenced, present 1/3
Inputs: week (string)   Volatile result paths: 15   Edges: 2

$ uv run amber align weekly-report --json | python -c "...assert s['steps'] and s['inputs']"
ACCEPT align json: 5 steps, ['week']
```

Full suite: 147 passed, 1 skipped. ruff + pyright (strict for `trace/`, `align/`) clean.

**Known limits (v0.1)**: a numeric arg that merely varies across traces is classified as a
transform (per spec); if the synthesizer cannot derive it, P4 downgrades the hole to judgment.
Loop/pagination collapsing is deferred to v0.2 (`repeated_call` warning only).

## 2026-11-02 — P4 Compile

**Decisions**: D3 (synthesis via `anthropic` `messages.parse` structured outputs; `cli` backend
with `claude -p --json-schema` as the no-API-key fallback; `recorded` backend for tests),
D4 (hybrid codegen: skeleton from code, model fills typed holes only), D13 (Python output).

**Built**

- `runtime/program_api.py`: `Context` protocol, `@program(workflow, version)` capturing the
  `Inputs`/`Output` models, `load_program(path)`.
- `compile/holes.py`: hole collection (`transform_step<i>` with params `inputs, r0..r<i-1>`;
  `judgment_step<i>` with context keys = inputs + referenced/echoed/mentioned steps + earlier
  `observe` steps; `gate_step<i>` for unknown predicates), examples ordered by trace, shrinking
  (20 items / 500 chars / 8 KB), verification (transforms: sandboxed execution, numeric
  tolerance 1e-9, order-insensitive list-of-dicts with a note; judgments: exact field set,
  ≤ 1200 chars, only available context keys, no tool-name/trace-id leaks, schema validates every
  recorded value), `downgrade_to_judgment`.
- `compile/sandbox.py` (AST allow-list, curated builtins, restricted `__import__`, thread
  timeout), `compile/lint.py` (generated-code rules), `compile/manifest.py` (`Manifest`,
  genson result schemas, budgets = `max_calls_factor × max recorded calls`),
  `compile/codegen.py` + Jinja templates (deterministic `program.py`, hoisted fill imports,
  wrapped prompt literals, nested-arg reconstruction, f-string templates, conditional
  `if`/gate blocks, `Output` from the final result's scalar keys + judgment text),
  `compile/synthesizer.py` (structured `TransformFill`/`JudgmentFill`, repair loop with the
  exact mismatch, transform → judgment downgrade, `CompileError` on judgment failure),
  `compile/versions.py` (bump on skeleton change / `--refresh` / unusable previous),
  `compile/report.py` (`REPORT.md`), `compile/compiler.py` (orchestration; the program is
  imported once to put its JSON schemas into the manifest).
- `models/`: `ModelBackend` protocol, `anthropic_api`, `claude_cli` (`--json-schema`,
  one retry), `recorded` (fixtures keyed by `hole_id`/`step_id`, attempt-indexed), `select`.
- CLI: `amber compile`, `amber programs ls|show|diff`.
- Fixtures: `tests/fixtures/model_fills/{weekly-report,gate}.json`; golden
  `tests/unit/compile/golden/weekly_report_v1.py` (regenerate with `pytest --update-golden`).
- Tests: 55 (sandbox/lint, holes, codegen golden + a fake-context execution of the generated
  program against recorded results, synthesizer repair/downgrade/failure, versions/manifest,
  model backends, CLI).

**Spec notes**: hole examples and `verified_on` follow the trace order, not the alignment
anchor order; `context_keys` include all earlier `observe` steps (§A5) which is why the
weekly-report judgment sees `r0..r3`. Predicate derivation prefers `==` / `>` / `<` over `!=`.

**Acceptance (2026-11-02)**

```
$ uv run pytest -q tests/unit/compile
40 passed   (+ models 5, CLI 4)

$ AMBER_MODEL_BACKEND=recorded uv run amber compile weekly-report --json | python -c "..."   # scratch project
ACCEPT compile: 1 2 ['transform_step3', 'judgment_step4']

$ test -f programs/weekly-report/v1/program.py && python -c "import ast; ast.parse(open(...).read())"
ACCEPT program parses

$ AMBER_MODEL_BACKEND=recorded uv run amber compile weekly-report
 hole             kind       step  attempts  result
 transform_step3  transform     4         1  accepted
 judgment_step4   judgment      5         1  accepted
  holes filled 2/2 · usable: no — run `amber verify weekly-report`
```

Full suite: 202 passed, 1 skipped. ruff + pyright clean (strict for `compile/`).

## 2026-11-02 — P5 Verify (cassette replay)

**Built**

- `runtime/backends/cassette.py`: `CassetteBackend(trace, manifest, strict_order)` — expected
  calls are the kept steps' calls of that trace (`manifest.trace_calls`, so pruned
  exploratory calls are skipped) prefixed by `prefix_calls` for resumed traces; args are
  canonicalized and masked with `manifest.volatile_paths` on both sides; `ReplayDivergence`
  (exit 3) carries `step_idx`, expected/actual, a ≤ 40-line unified diff and a hint (order
  differs / argument path / extra call / skipped call); `--unordered` matches any pending
  call; `llm()` serves the recorded unknown leaves (`LlmStep.field_paths`) validated against
  the schema, gates return `go = step present`, and context keys are checked against the
  manifest; `now()` is the trace's `created_at`.
- `runtime/verify.py`: `replay`, `ReplayReport`/`VerifyReport` (table, rows, JSON),
  `verify_version`, `mark_usable` (only a full-source-set verify can flip `usable`; also
  rewrites the REPORT.md verify section), `replay_from_paths` for the generated
  `tests/test_replay.py`. Output comparison: Output fields vs the recorded final result keys.
- `runtime/judgment.py`: runtime judgment call (prompt + shrunk context JSON carrying
  `step_id`) and `--live-llm` agreement (`exact` for enums/bools/numbers, `JudgeOut` for
  free text); never affects `usable`.
- Compile hook: `amber compile` now runs verify on all source traces and marks the version
  usable; the manifest gained `trace_calls`, `trace_inputs` (declared + inferred values per
  trace) and `LlmStep.field_paths`.
- CLI `amber verify <wf> [--version N] [--trace ID]... [--live-llm] [--unordered] [--json]`,
  exit 3 on failure (via `cli._output.fail`, so the code is visible even when the Typer app is
  invoked directly). The recorded model backend now finds `step_id` inside rendered prompts.

**Acceptance (2026-11-02)**

```
$ uv run pytest -q tests/unit/runtime/test_cassette.py
9 passed   (wrong tool, wrong arg, extra call, missing/leftover, volatile masked, unordered,
            pruned calls skipped, recorded llm + validation, resumed prefix)

$ AMBER_MODEL_BACKEND=recorded uv run amber compile weekly-report && uv run amber verify weekly-report
  holes filled 2/2 · usable: yes
│ t1 │ 5/5 │ 1 │ PASS │   │ t2 │ 5/5 │ 1 │ PASS │   │ t3 │ 5/5 │ 1 │ PASS │   3/3 PASS
verify exit=0

$ uv run amber verify weekly-report --trace t_broken; echo $?
│ t_broken │ 1/6 (+5 left) │ 0 │ FAIL │ replay diverged at call 1: expected shop.top_products, got shop.list_orders …
error: 1 of 1 traces diverged
3
```

Full suite: 221 passed, 1 skipped. ruff + pyright clean.

## 2026-11-02 — P6 Run (live backends, guards, ledger, runner, scheduler) + P8 servers (early)

**Decisions**: D8 (dry-run by default; `--allow-writes` explicit), D11 (minimal in-process
scheduler; cron/GitHub Actions recommended in docs), D9 (mock servers, synthetic data).

**Built**

- `runtime/backends/live.py`: one `mcp.Client` per `[servers.*]` (stdio with the full env and
  the project dir as cwd, or streamable HTTP), tool listing cached, effects from annotations,
  schema-drift events (missing tool / input-schema hash change → hard), `ToolError` with a
  transient-pattern retry hint.
- `runtime/backends/builtins.py`: `Read/Write/Glob/Grep/WebFetch/Bash` under the policy
  (paths confined to the work dir or `[builtins].read_roots`; WebFetch http(s) only, 10 MB,
  30 s, HTML tag-stripped; Bash must fully match the recorded template regex, deny-list, pipes
  only if recorded, scrubbed env, 120 s). Other builtins → compile-time `UnsupportedBuiltin`.
- `runtime/guards.py`: allowlist, budgets (calls, llm tokens, wall-clock), write-effect
  dry-run with the manifest's masked placeholder (effect `unknown` is treated as write),
  result-shape validation against the genson schemas (missing key / type → hard, extra key /
  empty list → soft), llm confidence (soft) and validation (hard after one retry), error
  guard, schema drift (hard stops before the first call). `GuardStop` → exit 4.
- `runtime/ledger.py`: the frozen SQLite schema (`runs`, `run_steps`, `guard_events`,
  `llm_calls`, `decision_records`, `schedules`) over the single-writer `Db`.
- `runtime/runner.py`: `LiveContext` sync facade over an event loop in a worker thread; step
  cursor maps calls to manifest steps; transient tool errors retried once; every run, step,
  guard event and llm call is ledgered; `RunResult` with `agent_turns = 0`.
- `runtime/scheduler.py` + `amber schedule add|ls|rm`, `amber scheduler [--once]`.
- Manifest gained `placeholder_results` and `bash_templates`.
- CLI `amber run <wf> [--input k=v] [--version N] [--dry-run] [--allow-writes]
  [--fallback agent|none] [--timeout S] [--force] [--json]` (exit 4 on a guard stop).
- Examples (P8, parallel-safe part): `examples/_shared/{synth,mcp_common,server_slack}.py`,
  `examples/weekly-report/{server_shop.py,scenario.py,amber.toml,mcp.json,task.md,Makefile}`,
  `examples/README.md`. `make demo-offline` seeds the synthetic fixture recordings, compiles
  with the recorded model fills, verifies 3/3 and dry-runs against the live mock servers.

**Spec corrections**

- Schema hashes now ignore `title`/`description`/`$schema`/`examples` and empty `required`
  (`trace.canonical.normalize_schema`), and `ToolSchema.hash` is always recomputed from
  `input_schema`: hand-written and server-generated schemas for the same inputs hash the same,
  and traces survive changes to the hashing rule.
- `normalize_result` unwraps MCP's `{"result": …}` structured-content envelope for non-object
  returns, so live results have the shape the agent saw in the recording.
- `amber run --dry-run` is the explicit form of the default (writes never execute); it is
  mutually exclusive with `--allow-writes`.

**Acceptance (2026-11-02)**

```
$ uv run pytest -q tests/unit/runtime
43 passed   (guards table, builtins policy, ledger, cassette, verify, live backend with the echo
             server, runner against the mock shop/slack servers, scheduler)

$ cd examples/weekly-report && make demo-offline        # seed + compile + verify + run --dry-run
  holes filled 2/2 · usable: yes
│ t1 │ 5/5 │ 1 │ PASS │ … 3/3 PASS
  #1   shop.week_range              ok           46 ms
  #2   shop.list_orders             ok           14 ms
  #3   shop.top_products            ok            0 ms
  #4   shop.rfm_snapshot            ok           14 ms
  #5   llm:step4                    llm           0 ms  1584+85 tok
  #5   slack.post_message           dry-run       0 ms  write effect; pass --allow-writes to execute
Run weekly-report v1 → ok  (r_20261102_180004_418672, dry-run)
  steps 5 · llm calls 1 · tokens in 1,584 · out 85 · 1.9 s · agent turns 0

$ amber run weekly-report --input week=2026-W44 --allow-writes --json | python -c "...status=='ok' and agent_turns==0 and llm_calls<=2"
(covered by tests/unit/cli/test_run.py::test_run_dry_run_and_allow_writes; .outbox/slack.jsonl receives the post)
$ make drift && amber run … → exit 4, stop_kind result_shape (tests/unit/cli/test_run.py::test_run_guard_stop_exits_4)
```

Full suite: 257 passed, 1 skipped. ruff + pyright clean.

## 2026-11-02 — P7 Doctor + fallback loop

**Built**

- `doctor/checks.py`: `servers` + `schema_drift` (probe through the same `LiveBackend` the
  runner uses), `result_shape` (hard events in any recent run → hard; soft in ≥ 50 % → soft),
  `guard_stats` (stop rate ≥ 30 % soft, ≥ 60 % hard), `llm_health` (validation failures ≥ 20 %
  or low confidence ≥ 50 % → soft), `staleness` (newer traces not in the version), `program_lint`
  (lint + program hash). `doctor/diagnosis.py`: the decision table → `Diagnosis`
  (`healthy|degraded|broken`, `none|recompile|fallback|re-record`, rationale, next command).
  CLI `amber doctor <wf> [--runs N] [--no-probe] [--json]` → exit 5 unless healthy.
- `fallback/proposal.py` (`FallbackProposal` from a stopped `RunResult`: failing step, completed
  steps, completed/remaining write steps, proposed action from the stop kind),
  `fallback/sentry.py` (vetoes: `no_fallback_flag`, `daily_budget`, `writes_already_done`,
  `servers_unreachable`, `budget_exhausted`, `lint_failure`, `writes_not_allowed`),
  `fallback/arbiter.py` (table → `retry | fallback_resume | abort`; `fallback_full` always
  degrades to resume in v0.1; vetoed actions abort with the veto's reason; every decision is
  written to `decision_records` before anything acts), `fallback/agent_fallback.py` (resume
  prompt from `resume_prompt.md.j2`, relaunch via the `claude-stream` adapter with the
  recorded `--mcp`/`--allow`/`--permission-mode` now stored in `Trace.harness` and
  `manifest.recording`, the compiled run's completed calls spliced in front of the agent's
  events, `origin="fallback"`, `resumed_from_step`), `fallback/pipeline.py` (orchestration;
  `retry` re-runs once and re-decides).
- Runner keeps `completed_calls` (args + results) for the resume; ledger `set_agent_turns`;
  the cassette no longer double-counts `prefix_calls` that were spliced into the events.
- `amber run --fallback agent` prints the decision chain, saves the fallback trace and points
  to `amber compile --refresh`; `compile --refresh` folds the resumed trace in (v2 verifies
  against all traces, including the fallback one).

**Acceptance (2026-11-02)** — offline parts run in `examples/weekly-report` with the recorded
model backend; the live agent step is exercised by the unit tests through a fake `claude`.

```
$ uv run pytest -q tests/unit/doctor tests/unit/fallback
22 passed   (decision tables, veto rules, arbiter, resume prompt golden, splice, pipeline with a
             fake claude → fallback trace → compile --refresh → v2 verifies 4/4, CLI run --fallback agent)

$ make drift && uv run amber run weekly-report --input week=2026-W44 --allow-writes; echo $?
  #2   shop.list_orders             stopped      15 ms  $[0]: missing key 'total'
Run weekly-report v1 → stopped  (r_20261102_181356_6111c3, live)
error: run … stopped: result_shape — $[0]: missing key 'total'
4

$ uv run amber doctor weekly-report; echo $?
Doctor weekly-report v1  → degraded
  ✓ servers        2/2 reachable
  ✓ schema_drift   5 tool schemas match
  ✗ result_shape   $[0]: missing key 'total' in 1/2 recent runs (hard)
  ~ guard_stats    stop rate 1/2 (50%)
  ✓ llm_health     0 validation failures, 0 low-confidence in 1 calls
  ✓ staleness      v1 covers all traces
  ✓ program_lint   program.py passes lint and matches the manifest
Recommendation: fallback — run `amber run weekly-report --fallback agent`, then `amber compile weekly-report --refresh`
5

$ uv run amber doctor weekly-report --json | python -c "...assert d['status']!='healthy' and d['recommendation'] in ('fallback','recompile')"
ACCEPT doctor: degraded fallback
```

Live acceptance (`amber run … --fallback agent` with the real `claude`, then `compile --refresh`
and `verify`) is wired (`make heal`) but was not run here: it needs a logged-in `claude` and
spends tokens. The PLAN's price-watch variant of this story needs the P8 price-watch example.

Full suite: 279 passed, 1 skipped. ruff + pyright clean.

## 2026-11-02 — P8 Examples (offline parts)

**Built**

- `examples/issue-triage/`: `server_github.py` (`list_issues`, `get_issue`, `search_issues`,
  `add_labels` [write], `add_comment` [write]; scenario B switches `area:*` → `kind/*` and
  rejects unknown labels), `scenario.py` (40 seeded issues over 10 days, 6 areas, 4 duplicate
  pairs), `amber.toml`, `mcp.json`, `task.md`, `Makefile` (record with `since=2026-10-27|28|29`).
- `examples/price-watch/`: `scenario.py` writes `site/index.html` (six fictional lanterns with
  `.price`/`.stock` spans; scenario B changes three prices, one stock and renames `.price` →
  `.cost`) and `site/previous.json`; `amber.toml` / `mcp.json` use `npx -y @playwright/mcp@latest
  --headless` (Node ≥ 18) plus the shared Slack mock as `alerts`; `Makefile` with `serve`,
  `record`, `drift`, `heal`, `demo`.
- `examples/README.md` documents what each example demonstrates and the expected output.
- Tests (`tests/unit/examples`): generators deterministic by seed, `list_tools` goldens for
  shop / slack / github, scenario B behaviour (shop `amount`, github label rejection, price-watch
  `.cost`), outbox.

**Not run (live)**: `make demo` for issue-triage and price-watch (3 `claude -p` recordings each)
and `make demo && make drift && make heal` for price-watch need a logged-in `claude` CLI (and Node
for Playwright). weekly-report's `make demo-offline` covers the compile → verify → run path
offline; its live `make demo` is wired the same way.

Full suite: 282 passed, 1 skipped. ruff + pyright clean.

## 2026-11-02 — P9 Web panel

**Decisions**: D10 (FastAPI + Jinja2 + vendored htmx 2.0.4, server-rendered SVG; no Node build,
no CDN; binds 127.0.0.1, no auth).

**Built**

- `ui/app.py` (`create_app(root)`, `run_ui`): pages `/`, `/programs`, `/programs/{wf}`,
  `/programs/{wf}/v{n}` (steps, llm steps, transforms, manifest, pygments-highlighted
  `program.py`, REPORT.md), `/runs[?workflow=]`, `/runs/{id}` (step timeline, guard events,
  llm calls, decisions), `/doctor/{wf}` (GET shows the session's last diagnosis; POST runs the
  doctor and returns the `_doctor_result.html` fragment via htmx; `?probe=0` skips the server
  probe), `/decisions[?workflow=]`, `/traces/{id}`; JSON twins under `/api/*` built from the
  `ui/models.py` Pydantic models.
- `ui/data.py` (`Panel`): overview per workflow (latest version, usable, runs in 7 d, stop rate,
  tokens saved = median agent `total_input` of the source traces × runs − compiled llm tokens),
  chart rows; `ui/charts.py`: tokens-per-run grouped bars with the dashed agent baseline and
  guard-events stacked bars (≤ 24 px bars, 4 px radius on the data end, 12 %-opacity grid,
  `<title>`/`<desc>`, colours from CSS classes so light/dark follow `prefers-color-scheme`);
  every chart has a `<details>` table twin. `static/app.css` is 60 lines.
- CLI `amber ui [--port 8787] [--open]` (lazy import with a hint for the `[ui]` extra).
- Tests (`tests/unit/ui/test_routes.py`): every route 200 and mentions the workflow, 404s,
  static assets, API twins validate against the models, doctor POST fragment, SVG parses.

**Acceptance (2026-11-02)**

```
$ uv run pytest -q tests/unit/ui
14 passed

$ uv run amber ui --port 8787 & sleep 2 && curl -s localhost:8787/ | grep -q "Programs" && kill %1
ACCEPT: panel serves / with the Programs nav          # run through a child-process helper on Windows
api/overview: [{"agent_median_tokens": 18085, "latest_version": 1, "runs_7d": 0, ...}]
```

Full suite: 296 passed, 1 skipped. ruff + pyright clean.

## 2026-11-02 — P10 Bench, README, docs, release prep

**Decisions**: D1 default kept (Amber / `amber-compiler` / import `amber` / CLI `amber`; the
GitHub repository already carries the name). Release artefacts (tag, PyPI, GIF) are left to the
maintainer: they need a logged-in `claude` CLI, `vhs`, and the PyPI trusted publisher.

**Built**

- `bench/run_bench.py` (+ `amber bench` wrapper, `make bench`): the protocol from
  `docs/09-launch.md` — N `amber record` runs (input + cache_read + cache_creation tokens, output
  tokens, cost, wall, turns) vs N `amber run --allow-writes` runs (llm tokens, wall, calls, agent
  turns, tool-call fidelity vs the recording for the same input) → `bench/results.json` and
  `bench/results.md` (medians, min/max, model ids, date, commit, command, mode). `--offline`
  takes the existing recordings as the baseline and runs the compiled program with the recorded
  model backend, so no model is called.
- `README.md` per the `09-launch.md` skeleton: hero, badges, why, install, 6-command story,
  mermaid pipeline, what comes out, measured table (offline-labelled), supported inputs, what it
  can't do yet, related work, FAQ pointer, docs map, contributing/license.
- `docs/user/`: quickstart, concepts, cli (generated with `typer src/amber/cli/app.py utils docs`),
  adapters, servers, faq; linked from `docs/index.md`.
- `scripts/demo.tape` + `scripts/demo_gif.sh` (vhs; the GIF itself is not recorded yet).
- `make demo-offline` at the repository root (delegates to the example).

**Finding**: the first offline bench run went through the `auto` model backend, which picked the
logged-in `claude` CLI for the judgment step; every call carried ~32k input tokens (the CLI's own
system prompt), tripped the example's `max_llm_tokens = 20000` guard and the runs ended
`stopped (budget: llm token budget exceeded)`. Three such runs were made before the script was
changed to force the recorded backend in `--offline` mode. Documented in `bench/README.md`, the
FAQ and the README limitations.

**Acceptance (2026-11-02)**

```
$ uv run python bench/run_bench.py --example weekly-report --runs 3 --offline && test -s bench/results.md
weekly-report: agent median 18,085 tok, compiled median 1,669 tok
wrote bench/results.md          # OFFLINE mode; `--all --runs 3` (live) needs the claude CLI

$ uv build && uv run twine check dist/*
Successfully built dist/amber_compiler-0.1.0a0.tar.gz
Successfully built dist/amber_compiler-0.1.0a0-py3-none-any.whl
Checking dist/amber_compiler-0.1.0a0-py3-none-any.whl: PASSED
Checking dist/amber_compiler-0.1.0a0.tar.gz: PASSED

$ uvx --from ./dist/amber_compiler-0.1.0a0-py3-none-any.whl amber --version
amber 0.1.0a0
```

Full suite: 296 passed, 1 skipped. ruff + pyright clean.

**Deferred (need the maintainer)**: live `make demo`, live bench for all three examples
(`--all --runs 3`), `docs/assets/demo.gif`, CHANGELOG v0.1.0 + version bump + tag + TestPyPI/PyPI
via `release.yml`, post-release `uvx --from amber-compiler amber --version` on a clean machine.

## 2026-11-02 — Live run on a claude.ai subscription, fixes, measured bench, 0.1.0

**Setup.** Windows 11, claude CLI 2.1.316 logged in with claude.ai (no `ANTHROPIC_API_KEY`).
Agent recordings on `claude-sonnet-5-5`, passed explicitly with `--model` because the user asked
not to spend tests on the default (Fable) model; hole synthesis on `claude-sonnet-5-5`, run-time
judgments on `claude-haiku-5-5`, both through the `cli` backend. Each example went through
`make record` (3 runs) → `amber align --show` → `amber compile` → `amber verify` → `amber run`
on an input nobody recorded.

**Problems the live runs exposed** (one commit each; every code fix has a regression test):

1. CLI backend: `--max-turns 1` always ended in `error_max_turns` (structured output takes a
   second turn); the call carried ~24k tokens of Claude Code context; prompts on argv hit the
   Windows command-line limit. Now `--max-turns 2`, no built-in tools, no dynamic system-prompt
   sections, no session file, system prompt via file, prompt via stdin (~1.2k overhead).
2. The fallback would relaunch `claude` without `--model` (the user's default). Recordings now
   store the pass-through flags; the manifest and the fallback reuse them.
3. Claude Code's `ToolSearch` was recorded as a builtin step → skipped as harness bookkeeping.
4. Account-level MCP connectors (claude.ai Docs, 170 Higgsfield tools) entered the agent's
   context and the trace → `amber record --mcp` adds `--strict-mcp-config`. The three
   weekly-report recordings made before this were discarded (they would inflate the baseline).
5. `tests/unit/cli/test_run.py` failed when run alone (`UnsupportedOperation: fileno`): mcp binds
   `sys.stderr` at import time → explicit errlog for every stdio server. All test files now pass
   in isolation too.
6. The previous ISO week (`2026-W41`) became a required inferred input → ISO week, ordinal and
   year-month dates are transforms when another date can derive them.
7. `top_products n=5` resolved to `Ref(3,$.result[22].f)`, a coincidence in all three runs → a
   constant prefers `Const` over a list-position `Ref`.
8. Judgment context cut every list to 20 items (20 of 75 orders) → whole values within 40 KB.
9. Generated programs passed the `Inputs` model to transforms verified with a dict → replay
   failed with `'Inputs' object is not subscriptable`.
10. Claude Code shows MCP structured content `{"result": [...]}` to the agent as text while the
    live backend unwrapped it → the first live run stopped on `result_shape: $: type array,
    recorded object`. One `unwrap_envelope` for every path; stored traces were migrated with the
    same function.
11. Store `sync()` deleted traces it had just re-indexed under another spelling of their path.
12. The first transform attempt was rejected only for `from typing import Any` → future imports
    and plain typing names accepted (`import typing` stays blocked: `get_type_hints` evaluates
    strings).
13. Haiku through the CLI thought 9.9k tokens for one judgment and tripped the 20k budget →
    `--effort low` for short answers (6.4k, counts verified against a recount); example budgets
    40k; `amber.toml` budgets apply at run time without a recompile.
14. The doctor mixed program versions (a healed v2 stayed degraded) and printed a soft detail next
    to a hard verdict → version-scoped statistics.
15. A judgment told Slack "r5 contains no rows" → the system prompt forbids context keys.
16. issue-triage: per-issue `add_labels` (17, 13 and 10 calls) cannot compile without loops
    (v0.2) → batch writes in the mock server, variable-length list arguments mined as one leaf,
    nested judgment models turned into plain JSON for tool calls. The generator made some
    duplicates older than their originals → fixed.
17. Conditional-step predicates accepted one-trace "equalities" and whole page snapshots → they
    need two supporting traces and a flag-like value; otherwise the step gets a gate.
18. price-watch: scenario A never alerts, so the program could never post → scenario A2 (a
    price-update day) and per-run scenario tags. The agent read Playwright's timestamped snapshot
    files with `Read` and alternated navigate and fetch → the task names the browser tools and the
    recordings disallow `Read,Glob,Grep` (six recordings discarded).
19. Gate fills could never verify, because a recording has no `reason` → placeholders for fields
    a recording cannot contain.
20. A conditional step's judgment output raised `cannot access local variable 's4'` on a quiet
    day → declared before the `if`, optional in `Output`.
21. Prompts that named an input (`product`) were rejected as unknown context keys.
22. The CLI backend dropped a rejected first attempt's usage, and the CLI's cost was not recorded.

23. Found by CI, not locally: `amber scheduler --once` (and `amber run`) resolved a model backend
    before knowing one was needed, so a machine without the claude CLI or an API key failed with
    "no model backend available". Both now resolve it optionally. The logged-in CLI on PATH had
    hidden this, so the test fixture now hides a real `claude` for non-live tests.

**Finding: price-watch's planned drift is absorbed.** @playwright/mcp's snapshot is an
accessibility tree without CSS classes and `browser_evaluate` returns markdown text, so the
`.price` → `.cost` rename changes nothing the program reads. Under scenario B it posted the
correct alert without a guard stop. The self-healing loop was run on weekly-report instead
(spec note in `docs/06-examples.md`).

**Live outputs (2026-11-02, trimmed)**

```
weekly-report  verify v1 3/3 PASS
               run week=2026-W40 (unrecorded): steps 7 · llm calls 1 · tokens in 21,170 · out 8,072 · 31.5 s · agent turns 0
               (segment counts 19→29, 43→37, 50→43, 6→10 and the top-5 revenue match a recount)
               make drift; run week=2026-W41 → stopped: result_shape — $[0]: missing key 'total' (exit 4)
               doctor → degraded, recommendation fallback (exit 5)
               run --fallback agent → fallback_resume from step 3; trace t_20261102_204011_cc43a1 (8 calls, 8 agent turns)
               compile --refresh → v2; verify v2 4/4 PASS; run week=2026-W39 ok (0 agent turns); doctor v2 healthy
issue-triage   verify v1 3/3 PASS (labels and duplicate comments compiled to deterministic transforms)
               run since=2026-10-28 (unrecorded): steps 4 · llm calls 1 · tokens in 10,398 · out 2,147 · 12.4 s · agent turns 0
price-watch    verify v2 3/3 PASS (v1 failed replay on the quiet day: fix 20)
               A2 product=torch-iron (unrecorded): alert "Iron Garden Torch (torch-iron) $27.00 -> $29.50" · 2 llm calls · 15k tokens
               A product=lantern-tin: the gate said no, nothing posted · 1 llm call · 7k tokens
               B product=all: correct alert for 3 price changes + newly out of stock, no guard stop
```

**Acceptance (2026-11-02)**

```
$ uv run python bench/run_bench.py --all --runs 3 --baseline existing && test -s bench/results.md
issue-triage: agent median 153,944 tok, compiled median 11,892 tok, statuses ['ok']
price-watch: agent median 165,225 tok, compiled median 14,862 tok, statuses ['ok']
weekly-report: agent median 174,076 tok, compiled median 29,472 tok, statuses ['ok']
wrote bench/results.md
```

The plan's `--all --runs 3` form re-records nine agent runs; the baseline above is the nine
recordings made earlier the same day with identical flags (`--baseline existing`).

```
$ uv build && uv run twine check dist/*
Checking dist/amber_compiler-0.1.0-py3-none-any.whl: PASSED
Checking dist/amber_compiler-0.1.0.tar.gz: PASSED
$ uvx --from ./dist/amber_compiler-0.1.0-py3-none-any.whl amber --version
amber 0.1.0
$ uv run pytest -q --cov=amber.trace --cov=amber.align --cov=amber.compile --cov=amber.runtime.backends.cassette
338 passed, 1 skipped · TOTAL 92%
```

Full suite: 338 passed, 1 skipped. ruff + pyright clean.

**Not done (needs the maintainer):** the demo GIF (`vhs` and `ffmpeg` are not installed here);
PyPI publishing (configure the trusted publisher for `amber-compiler`, then set the repository
variable `PYPI_PUBLISH=true`; the repository is private, so publishing would make the source
public) and the post-release `uvx --from amber-compiler` check. A leftover `amber ui --port 8787`
process from the P9 smoke test still holds `.venv/Scripts/amber.exe`; close it before `uv sync`.

**Release (2026-11-03)**

```
CI 41525188353 on 95de7c7: gitleaks, py3.12/py3.13 × ubuntu/macos: success (5/5)
git tag -a v0.1.0 && git push origin v0.1.0
release.yml 41527017634: build success (uv build + wheel smoke), publish skipped (PYPI_PUBLISH unset)
gh release create v0.1.0: amber_compiler-0.1.0-py3-none-any.whl, amber_compiler-0.1.0.tar.gz
https://github.com/enestanerr/Amber/releases/tag/v0.1.0
```
