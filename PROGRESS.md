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
