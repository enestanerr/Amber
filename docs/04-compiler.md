---
title: "04 — Compiler: align → codegen → holes → synthesize → verify"
tags: [amber/spec, compiler]
phases: [P3, P4, P5]
status: spec
created: 2026-11-02
---
# 04 — Compiler: align → codegen → holes → synthesize → verify

The compiler is deliberately boring: structure comes from the recordings, the model only
fills typed holes, and every fill is checked against the recordings. This document is the
specification for `amber.align`, `amber.compile` and `amber.runtime.verify`.

---

## A. Align (`amber.align`)

Input: `traces: list[Trace]` for one workflow (successful, unless `--include-failed`).
Output: `Skeleton`.

### A1. Call sequences

For each trace build `calls = [c for c in events if c.kind == "tool_call"]` and
`thoughts_before[c.idx]` = concatenated `Thought.text` since the previous call (≤ 2,000 chars).

### A2. Sequence alignment

Pairwise Needleman–Wunsch on `tool` names:

- match (same `tool`): `+2`, plus `+1` if `args_fingerprint` equal;
- mismatch (different tools): `-1`; gap: `-1`.
- Ties resolved toward matching (prefer diagonal), then gap in the shorter sequence.

Progressive multi-alignment: anchor = longest trace; align each other trace to the anchor's
*column list*; a column is `dict[trace_id, call_idx | None]`. Columns with a call in **every**
trace are `core`; others `optional`.

Test cases (unit): identical sequences → all core; one extra exploratory `builtin.Read` in
one trace → one optional column; reordered independent reads (A,B vs B,A) → two core columns
+ warning `reordered_steps` (v0.1 keeps the anchor order); repeated identical calls
(pagination: `list_orders` ×3) → three columns (no collapsing in v0.1; record a warning
`repeated_call` suggesting a loop in v0.2).

### A3. Provenance mining

For every column and every leaf `(path, value)` in the anchor call's `args`, resolve, in
order, the first kind that holds **in every trace where the column has a call**:

| kind | rule | compiled as |
|---|---|---|
| `Input(name)` | value equals `trace.inputs[name]` (string compare after `str()`; numbers compare numerically) | `inputs.name` |
| `Ref(col, path)` | value equals a leaf of an **earlier** column's result at the same `path` in that trace (first match by column order, then by shortest path) | `r<col>["..."]...` |
| `Template(parts)` | value is a string that contains ≥ 1 Input/Ref value as a substring (longest first, non-overlapping), with the literal remainder identical across traces; parts = literals + refs | f-string |
| `Const(value)` | identical in all traces | literal |
| `Unknown` | otherwise | hole (transform or judgment) |

Rules: leaf comparisons of strings are exact except trailing whitespace; a `Ref` to a
*list* leaf requires an exact index (`[0]`) — if the index differs across traces but the
value equals the leaf satisfying a simple predicate (e.g. the element with `max(total)`),
leave `Unknown` (the transform hole will express the selection). Ignore keys whose values are
≥ 20 KB (store as `Unknown` with `large=True`, which forces a judgment/transform via hole
with truncated context).

> **Note (2026-11-02, live recordings):** when a value is identical in every trace (so `Const`
> holds) and the best `Ref`/`Template` reaches it through a list index, `Const` wins. A list
> position explains a constant no better than the literal and breaks when the list reorders: in
> the first live weekly-report recordings `top_products n=5` matched an unrelated
> `rfm_snapshot $.result[22].f == 5` in all three runs. Varying values keep the order above.

> **Note (2026-11-02):** a list argument whose length differs across a column's calls (a batch
> write: 17, 13 and 10 label updates in three recordings) is mined as **one** leaf at the list's
> path, with a `batch_arg` warning. Per-index leaves would compile to a fixed-length list that
> cannot serve another input; as one leaf it resolves to `Const` when identical or else to a
> single hole that produces the whole list. Fixed-length lists keep per-index leaves. Loops over
> per-item calls remain v0.2.

Provenance also produces **edges**: `col_j → col_k` if any arg of `k` references `j`.

### A4. Classification

Per column (step):

| kind | condition | notes |
|---|---|---|
| `deterministic` | all leaves ∈ {Input, Ref, Template, Const} | direct `ctx.tool(...)` |
| `judgment` | ≥ 1 `Unknown` leaf that is (a) a string ≥ 24 chars, or (b) a short string from a small set that varies across traces (≤ 3 distinct values, e.g. labels), or (c) `thoughts_before` contains decision cues (`decide|choose|classify|summar|judg|seems|looks like|I think`) | `ctx.llm(...)` produces the unknown leaves |
| `transform-needed` | ≥ 1 `Unknown` leaf that is numeric, boolean, a date/ISO string, or a structured value (list/dict), and no judgment trigger | transform hole produces the unknown leaves |
| `conditional` | column is optional (present in some traces) | predicate: `Ref(col,path) <op> literal` if an earlier result leaf separates present/absent traces (`== / != / > / < / non-empty`), else `Unknown` → judgment hole returning `bool` |

A step can be both `conditional` and one of the first three. Mixed `Unknown` leaves (one
string ≥ 24 chars, one numeric) → `judgment` wins for the string leaf; numeric leaf gets its
own transform hole.

> **Note (2026-11-02):** "date/ISO string" covers the ISO 8601 calendar date (with optional
> time), week date (`2026-W41`, `2026-W41-3`), ordinal date (`2026-285`) and year-month
> (`2026-10`). A date-like leaf is a transform trigger only when every trace offers a date to
> compute it from (a declared input or an earlier result leaf); otherwise it falls through to
> §A6 and becomes an inferred input. Live recordings asked `week_range` for the previous week
> (`2026-W41` for input `2026-W42`), which must be a transform, while an undeclared first
> `week` argument must stay an inferred input.

### A5. Pruning (dead-step elimination)

Prune a column when **all** hold: its tool is read-only (`ToolSchema.effect == "read"`, or
builtin in `{Read, Glob, Grep, WebFetch}`, or name matches `^(get|list|search|read|fetch|
find|lookup)`), no edge leaves it, it is `optional`, and it is not the final step. Core
unreferenced read-only steps are kept but marked `observe: true` (they often exist to let the
agent "see" something — a judgment step later may need their result as context; the hole
builder adds them to candidate context).

### A6. Inferred inputs and volatile paths

- `Unknown` **arg** leaves that are short scalars (≤ 64 chars, numbers, dates) and vary across
  traces, with no transform/judgment trigger → promoted to inferred inputs named from the arg
  key (dedupe with suffixes); the leaf becomes `Input(name)`.
- Result leaves that differ across traces and have no outgoing edge → `volatile_paths` per
  step (e.g. `$.orders[*].created_at`, `$.id`). Masked in verify.

### A7. Skeleton model

```python
class Provenance(BaseModel):  # discriminated by kind
    kind: Literal["input","ref","template","const","unknown"]; name: str|None; col: int|None; path: str|None; parts: list[...]|None; value: Any|None; large: bool = False
class StepSpec(BaseModel):
    idx: int; tool: str; kind: Literal["deterministic","judgment","transform-needed"]; conditional: bool
    predicate: Provenance | None; args: dict[str, Provenance]  # flattened by JSON path
    present_in: list[str]; observe: bool = False; effect: str; schema_hash: str
    context_hint: list[int]        # earlier columns whose results the thoughts/args mention
    thoughts: dict[str, str]       # trace_id → thoughts_before
class Skeleton(BaseModel):
    workflow: str; traces: list[str]; inputs: dict[str, dict]   # name → {"type": "string", "source": "declared|inferred", "examples": [...]}
    steps: list[StepSpec]; pruned: list[StepSpec]; edges: list[tuple[int,int]]
    volatile_paths: dict[int, list[str]]; warnings: list[str]; hash: str
```

---

## B. Program API (`amber.runtime.program_api`)

Generated programs import only from `amber.runtime` and `pydantic`:

```python
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from amber.runtime import Context, program

class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week: str = Field(description="ISO week, e.g. 2026-W44", examples=["2026-W42", "2026-W43", "2026-W44"])

class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    posted: bool
    summary: str

class SummaryOut(BaseModel):            # judgment schema (hole-filled)
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=1200)
    confidence: float = Field(ge=0, le=1)

PROMPT_summary = """..."""              # hole-filled, ≤ 1200 chars, references only keys in `context`

def transform_rfm_shift(orders: list[dict[str, Any]], top: list[dict[str, Any]]) -> dict[str, Any]:
    ...                                 # hole-filled, verified against recordings

@program(workflow="weekly-report", version=1)
def run(inputs: Inputs, ctx: Context) -> Output:
    r1 = ctx.tool("shop.week_range", week=inputs.week)                                  # step 1
    r2 = ctx.tool("shop.list_orders", since=r1["start"], until=r1["end"])              # step 2
    r3 = ctx.tool("shop.top_products", since=r1["start"], until=r1["end"], n=5)       # step 3
    rfm = transform_rfm_shift(r2, r3)                                                  # step 4 (transform)
    s5 = ctx.llm("summary", SummaryOut, PROMPT_summary, context={"rfm": rfm, "top": r3})  # step 5 (judgment)
    r6 = ctx.tool("slack.post_message", channel="#eng", text=s5.text)                   # step 6 (write)
    return Output(posted=bool(r6.get("ok", True)), summary=s5.text)
```

`Context` (protocol):

```python
class Context(Protocol):
    def tool(self, name: str, /, **args: Any) -> Any: ...          # sync facade over async backend (runner drives an event loop)
    def llm(self, step_id: str, schema: type[T], prompt: str, context: dict[str, Any]) -> T: ...
    def now(self) -> datetime: ...                                 # recorded/replayed; never datetime.now() in programs
    def log(self, msg: str) -> None: ...
```

Generated code rules (enforced by an AST lint in `compile/lint.py`): imports only
`__future__`, `typing`, `pydantic`, `amber.runtime`, plus the transform allow-list; no
`datetime.now`, `random`, `os`, `subprocess`, `open`, `requests`, `httpx`; no `try/except`
around `ctx.tool` (guards handle errors); one `ctx.tool` per skeleton step, in skeleton order.

---

## C. Codegen (`amber.compile.codegen`, Jinja2 templates)

Deterministic: same skeleton hash → byte-identical `program.py` (golden-file tests).

- Variables: `r<idx>` for tool results; `s<idx>` for judgment outputs; transform results
  named from the hole id.
- Provenance → expression: `Input(name)` → `inputs.<name>`; `Ref(col, "$.a[0].b")` →
  `r<col>["a"][0]["b"]`; `Template` → f-string with the same expressions; `Const` → `repr`.
- Nested args are rebuilt from flattened paths (`{"filter": {"since": ...}}`).
- `conditional` steps: `if <predicate>:` block; with `Unknown` predicate → a judgment hole
  `Gate<idx>Out(BaseModel): go: bool; reason: str` and `if ctx.llm(...).go:`.
- `observe` steps are emitted (their results may feed judgment context).
- Effect annotation: `ctx.tool(..., )` calls for write-effect tools get a trailing
  comment `# effect: write`; the manifest carries the authoritative list.
- `Output`: fields = the final step's result keys that are scalars (≤ 6) + any judgment text;
  if the final step result is text, `Output(result: str)`.
- `manifest.json` and `REPORT.md` (skeleton table, hole fills with rationale, verify table,
  warnings, "review before live" note).
- `tests/test_replay.py`:
  ```python
  import pytest
  from amber.runtime.verify import replay_from_paths
  TRACES = ["t_...", "t_...", "t_..."]
  @pytest.mark.parametrize("trace_id", TRACES)
  def test_replay(trace_id):
      report = replay_from_paths(__file__, trace_id)
      assert report.status == "pass", report.summary()
  ```

---

## D. Holes and synthesizer (`amber.compile.holes`, `amber.compile.synthesizer`)

### D1. Hole kinds

| kind | signature | expected values | acceptance |
|---|---|---|---|
| `transform` | `def transform_<id>(<named refs/inputs>) -> <type>` | per trace: the `Unknown` arg leaves that the transform must produce | `fn(recorded inputs) == recorded outputs` for every trace (floats `abs_tol=1e-9`; lists of dicts compared after sorting by a stable key if order differs → warning) |
| `judgment` | `PROMPT_<id>: str`, `class <Id>Out(BaseModel)` with one field per `Unknown` leaf (+ `confidence: float`) | per trace: the recorded leaf values | schema validates every recorded value; prompt ≤ 1200 chars; prompt references only keys present in `context`; no tool names or trace ids leaked |
| `gate` | `class Gate<idx>Out(BaseModel): go: bool; reason: str` + prompt | per trace: present/absent | schema valid; prompt mentions the condition |

Context available to a judgment hole: results of `context_hint` columns (≤ 8 KB each after
`shrink()` — lists truncated to 20 items with a `"...+N"` marker, long strings to 500 chars),
the inputs, and `thoughts_before` (as examples of the agent's reasoning, never pasted into the
runtime prompt).

> **Note (2026-11-02, live recordings):** the 8 KB per-value `shrink()` applies to what the
> *synthesizer* sees (shapes and a few rows are enough to write a prompt or a transform). At
> **run time** the judgment gets whole values as long as the rendered context fits in 40 KB of
> compact JSON; beyond that the largest value is shrunk first (`runtime/judgment.py`
> `fit_context`). The live weekly-report summary aggregates over all 75 orders and both 120-row
> RFM snapshots (about 24 KB together); a 20-item cut made the judgment report wrong numbers.

### D2. Synthesizer prompts (structured outputs)

System prompt (fixed, in `synthesizer/prompts.py`):

```
You are the hole-filler of a compiler. You receive a typed hole and recordings of the values
it must produce. Write the smallest correct Python (for transforms) or the shortest prompt and
schema (for judgments) that reproduces the recordings and generalizes to similar inputs. Do
not invent data. Do not call tools or network. Use only: math, datetime, statistics, re, json,
itertools, collections. Explain your reasoning in `rationale` in one paragraph.
```

User message per hole (JSON): hole kind, signature, parameter examples per trace (shrunk),
expected outputs per trace, previous attempt + mismatch (on repair), style rules.

Output schemas: `TransformFill(python_source: str, rationale: str)`,
`JudgmentFill(prompt: str, fields: list[FieldSpec], rationale: str)` where
`FieldSpec(name, type ∈ {str,int,float,bool,list[str]}, description, enum: list[str]|None)`.
Fields are compiled into a Pydantic model with `extra="forbid"`.

Backend call: `ModelBackend.complete(schema, system, user, model=cfg.models.synthesis,
max_tokens=4000)` → for anthropic: `client.messages.parse(model=..., max_tokens=...,
system=..., messages=[{"role":"user","content": user}], output_format=schema)` →
`.parsed_output`. Structured outputs require `additionalProperties: false` on every object;
Pydantic `extra="forbid"` provides it (unit test asserts it recursively).

### D3. Repair loop

```
for attempt in range(max_repair + 1):
    fill = backend.complete(...)                      # attempt > 0 includes the mismatch report
    ok, report = verify_hole(hole, fill, traces)
    if ok: accept; break
else:
    if hole.kind == "transform": downgrade_to_judgment(hole); warn("transform_downgraded", hole.id)
    else: raise CompileError(hole.id, report)
```

Mismatch report: per trace, `expected` vs `actual` (canonical JSON, first 1 KB), exception
text if any, and the diff of leaf paths.

### D4. Sandbox for transform verification and runtime

`compile/sandbox.py`: parse with `ast`, reject `Import`/`ImportFrom` not in the allow-list,
any `Attribute` starting with `__`, names `exec, eval, compile, open, globals, locals,
getattr, setattr, delattr, breakpoint, input, __import__`; execute via `exec(code,
{"__builtins__": SAFE_BUILTINS, **ALLOWED_MODULES})` where `SAFE_BUILTINS` is a curated
dict (`len, range, min, max, sum, sorted, enumerate, zip, map, filter, abs, round, str, int,
float, bool, list, dict, set, tuple, any, all, isinstance, reversed, ValueError, KeyError`).
Timeout 5 s via a worker thread + `concurrent.futures` with cancellation on timeout (document
that CPU-bound infinite loops are a known limit; v0.2 uses a subprocess).

> **Note (2026-11-02):** besides the module allow-list, `from __future__ import …` and
> `from typing import <plain type names>` (`Any, Callable, Dict, FrozenSet, Iterable, Iterator,
> List, Literal, Mapping, Optional, Sequence, Set, Tuple, Union`) are accepted: the synthesizing
> model writes them habitually and a live compile lost a repair round to one. `import typing`
> and every other name from it stay rejected, because `typing.get_type_hints` evaluates
> annotation strings that the AST check never sees.

The same sandbox module validates `program.py` at load time (lint) — the program itself runs
in the normal interpreter (it has to call `ctx`), so REPORT.md tells users to review it.

### D5. Program versioning

`programs/<workflow>/v<N>/`. `compile` increments `N` when: skeleton hash changed, or
`--refresh`, or the previous version is not usable. `manifest.usable` is set only after
verify passes for all source traces. `programs ls` shows `usable`, `traces`, `llm steps`,
`transforms`, `created_at`.

---

## E. Verify (`amber.runtime.verify`, `amber.runtime.backends.cassette`)

### E1. Cassette semantics

```
expected = [(tool, canonicalize_args(args, volatile_paths[step]), result) for each call in trace order]
on ctx.tool(name, **args):
    req = (name, canonicalize_args(args, volatile_paths[current_step]))
    if strict_order: head = expected[pos]; require req == head[:2] else ReplayDivergence
    else: find first pending with == ; else ReplayDivergence
    pos += 1 (or mark consumed); return deep copy of recorded result
on ctx.llm(step_id, schema, prompt, context):
    value = recorded unknown leaves for step_id in this trace → schema(**value) (validate) ; also assert set(context.keys()) ⊆ allowed keys from manifest
on ctx.now(): return trace.created_at
after run: all expected consumed (else `leftover` failure); Output compared to recorded final leaves where mapped (else skipped with note)
```

Volatile masking applies to **both** sides: the recorded args and the program's args are
masked at the same paths before comparison, so `since=<volatile>` compares equal.

`ReplayDivergence` carries: `step_idx`, `expected (tool, args)`, `actual (tool, args)`,
`diff` (unified diff of canonical JSON, ≤ 40 lines), and a `hint`:
- different tool → "program order differs from recording; check skeleton column i";
- same tool, arg differs at a path with provenance `Ref` → "ref path changed; result shape
  of step j may be volatile — consider adding it to volatile_paths";
- extra call → "program made a call the recording did not; check conditional predicate";
- missing call → "program skipped a recorded call".

### E2. Live-LLM verification (`--live-llm`)

Calls the real judgment backend and reports agreement with the recorded value: exact match
for enums/bools/numbers; for free text, a `JudgeOut(same_meaning: bool, notes: str)` call to
the judgment model comparing recorded vs produced (cost shown). Never affects `usable`.

### E3. Reports

`ReplayReport(status: pass|fail, trace_id, consumed, expected, leftover, divergence,
output_match: bool|None, llm_steps: int, duration_ms)`; `VerifyReport` aggregates and renders
the table used by CLI, REPORT.md and the panel.
