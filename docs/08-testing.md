---
title: "08 — Testing strategy"
tags: [amber/spec, testing]
phases: [P0, P1, P2, P3, P4, P5, P6, P7, P8, P9]
status: spec
created: 2026-11-02
---
# 08 — Testing strategy

## Layers

| layer | location | runs in CI | needs |
|---|---|---|---|
| unit | `tests/unit/**` | yes | nothing external; fixtures only |
| property | inside unit (`hypothesis`) | yes | — |
| golden | `tests/unit/compile/golden/*.py` | yes | regenerate with `pytest --update-golden` (custom flag) |
| integration (offline) | `tests/integration/test_proxy_roundtrip.py`, echo server, cassette runs of examples | yes | `mcp` installed |
| live | marked `@pytest.mark.live` | no (manual / nightly) | `ANTHROPIC_API_KEY` or logged-in `claude`; Node for Playwright |

Unit tests must never call the network: the model backend fixture is `RecordedBackend`
(fills from `tests/fixtures/model_fills/*.json`); MCP tests use the in-memory transport
(`Client(server_object)`) or the echo stdio server.

## Must-have test cases (by phase)

**P1 trace**: canonical JSON idempotence (hypothesis over nested JSON), volatile masking with
`[*]`, `normalize_result` for string / block-list / structured_content / blob content,
`Trace.check()` invariants, store roundtrip and listing order.

**P2 adapters**: stream fixtures → golden traces (3), ANSI stripping, ignored event types,
`permission_request` abort path, non-zero exit → failure trace; transcript: parentUuid chain
with a branch (longest chain chosen + warning), foreign sessionId filtered, sidechain skipped,
compact boundary note; proxy: fragments → trace ordering by ts, blob offload, `is_error`
forwarding; `--latest` path encoding (`/a/b c` → `-a-b c`).

**P3 align**: NW alignment table cases (identical, extra optional, reordered, repeated),
provenance for each kind with a negative case each (e.g. same value found in input *and*
earlier result → Input wins), Template extraction (`"week=2026-W44"`), consistency rule
(provenance holds in 2/3 traces → Unknown), classification table (24-char threshold, enum-like
labels, decision cue), pruning (optional unreferenced read pruned; core kept as observe),
inferred inputs + volatile paths, skeleton hash stability.

**P4 compile**: codegen golden file for the fixture skeleton; nested arg reconstruction;
conditional + gate codegen; lint rejects forbidden imports/calls; sandbox AST rejection list
and timeout; hole verification: transform accepted/rejected/downgraded, judgment prompt
length and context-key checks; repair loop feeds mismatch and stops at `max_repair`;
structured-output schema has `additionalProperties: false` at every object level (recursive
walk of `model_json_schema()`); version bumping rules.

**P5 verify**: divergence cases (wrong tool, wrong arg, extra call, missing call, order with
`--unordered`), volatile masking both sides, RecordedLLM schema validation failure message,
output comparison, resumed-trace prefix preload.

**P6 runtime**: each guard's trigger table; dry-run placeholder shape; builtins path
confinement (`../` rejected), Bash template equality and deny-list, WebFetch size cap
(mock httpx transport); ledger single-writer lock (two threads → serialized); runner exit
codes.

**P7 doctor/fallback**: decision tables as parametrized tests; sentry rules; resume prompt
golden; recompile with a resumed trace.

**P8 examples**: generators deterministic by seed (same seed → same SQLite digest); each
server's `list_tools` schemas golden (so accidental tool changes are visible); scenario B
differences asserted.

**P9 ui**: routes 200; SVG valid; API models validate.

## Coverage and quality gates

- `pytest --cov=amber --cov-report=term-missing`; fail CI under 80 % for `trace`, `align`,
  `compile`, `runtime/backends/cassette.py`.
- `ruff` + `ruff format --check` + `pyright` clean.
- `make demo` runs manually before every release; the nightly workflow (optional) runs live
  tests with a repo secret and uploads bench results as an artifact (never commits them).

## Fixture hygiene

Fixtures are synthetic and small (< 50 KB each). Regenerate goldens only via the flag, review
the diff, commit. Never copy real transcripts into fixtures.
