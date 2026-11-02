"""Codegen: golden file, determinism, nested args, templates, conditionals, executable output."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from amber.align.models import Skeleton
from amber.align.skeleton import build_skeleton
from amber.compile.codegen import Fills, generate, path_expr, render_prompt
from amber.compile.holes import collect_holes
from amber.compile.lint import lint_program
from amber.compile.manifest import FieldSpec
from amber.runtime.program_api import load_program
from amber.trace.models import ToolCall, Trace
from amber.util.json import read_json
from tests.unit.align.conftest import mk_trace
from tests.unit.compile.conftest import FIXTURES, GOLDEN


def fixture_fills() -> Fills:
    data = read_json(FIXTURES / "model_fills" / "weekly-report.json")
    fills = Fills()
    fills.transforms["transform_step3"] = data["transform_step3"][0]["python_source"]
    j = data["judgment_step4"][0]
    fills.judgments["judgment_step4"] = (
        j["prompt"],
        [FieldSpec.model_validate(f) for f in j["fields"]],
    )
    return fills


def test_golden_program(
    weekly_skeleton: Skeleton, weekly_traces: list[Trace], update_golden: bool
) -> None:
    holes = collect_holes(weekly_skeleton, weekly_traces)
    gen = generate(weekly_skeleton, weekly_traces, holes, fixture_fills(), version=1)
    golden = GOLDEN / "weekly_report_v1.py"
    if update_golden or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(gen.source, encoding="utf-8", newline="\n")
    assert gen.source == golden.read_text(encoding="utf-8")
    ast.parse(gen.source)
    lint_program(gen.source)
    again = generate(weekly_skeleton, weekly_traces, holes, fixture_fills(), version=1)
    assert again.source == gen.source and again.test_source == gen.test_source
    assert '@program(workflow="weekly-report", version=1)' in gen.source
    assert (
        'r1 = ctx.tool("shop.list_orders", since=r0["start"], until=r0["end"])  # step 2'
        in gen.source
    )
    # transforms get ``inputs`` as the dict they were verified with, not the Pydantic model
    assert "# effect: write" in gen.source
    assert "t3 = transform_step3(inputs.model_dump(), r0, r1, r2)" in gen.source
    llm_line = next(line for line in gen.source.splitlines() if "s4 = ctx.llm(" in line)
    assert llm_line.strip().startswith('s4 = ctx.llm("step4", Step4Out, PROMPT_step4, context={')
    assert '"inputs": inputs.model_dump(), "r0": r0, "r1": r1, "r2": r2, "r3": r3' in llm_line
    assert [s.var for s in gen.steps] == ["r0", "r1", "r2", "r3", "r4"]
    assert [s.id for s in gen.llm_steps] == ["step4"]
    assert gen.llm_steps[0].context_keys == ["inputs", "r0", "r1", "r2", "r3"]
    assert gen.transforms[0].function == "transform_step3" and gen.transforms[0].produces == [
        "as_of"
    ]
    assert 'TRACES = ["t1", "t2", "t3"]' in gen.test_source


class FakeContext:
    """Serves recorded results of one trace in order and canned judgment values."""

    def __init__(self, trace: Trace, judgments: dict[str, dict[str, Any]]) -> None:
        self.calls = [
            e for e in trace.events if isinstance(e, ToolCall) and e.tool != "builtin.Read"
        ]
        self.judgments = judgments
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.trace = trace

    def tool(self, name: str, /, **args: Any) -> Any:
        self.seen.append((name, args))
        call = self.calls[len(self.seen) - 1]
        assert call.tool == name, (call.tool, name)
        return call.result

    def llm(self, step_id: str, schema: Any, prompt: str, context: dict[str, Any]) -> Any:
        assert prompt and "inputs" in context
        return schema.model_validate({**self.judgments[step_id], "confidence": 0.9})

    def now(self) -> Any:
        return self.trace.created_at

    def log(self, msg: str) -> None:
        pass


def test_generated_program_runs_against_recorded_results(
    weekly_skeleton: Skeleton, weekly_traces: list[Trace], tmp_path: Path
) -> None:
    holes = collect_holes(weekly_skeleton, weekly_traces)
    gen = generate(weekly_skeleton, weekly_traces, holes, fixture_fills(), version=1)
    path = tmp_path / "program.py"
    path.write_text(gen.source, encoding="utf-8")
    program = load_program(path)
    assert program.workflow == "weekly-report" and program.version == 1
    t1 = weekly_traces[0]
    summary = t1.calls[-1].args["text"]
    ctx = FakeContext(t1, {"step4": {"text": summary}})
    output = program.entry(program.parse_inputs({"week": "2026-W42"}), ctx)
    assert [name for name, _ in ctx.seen] == [c.tool for c in t1.calls]
    assert ctx.seen[3] == ("shop.rfm_snapshot", {"as_of": "2026-10-11"})  # transform fill at work
    assert ctx.seen[4][1] == {"channel": "#eng", "text": summary}
    dumped = output.model_dump()
    assert dumped["ok"] is True and dumped["text"] == summary and "ts" in dumped
    assert set(program.inputs_model.model_json_schema()["properties"]) == {"week"}


def test_dry_run_stubs(weekly_skeleton: Skeleton, weekly_traces: list[Trace]) -> None:
    holes = collect_holes(weekly_skeleton, weekly_traces)
    gen = generate(weekly_skeleton, weekly_traces, holes, Fills(), version=1)
    assert (
        "raise NotImplementedError" in gen.source and "TODO: fill hole judgment_step4" in gen.source
    )
    assert "class Step4Out(BaseModel)" in gen.source and "text: str = Field" in gen.source
    ast.parse(gen.source)
    lint_program(gen.source)


def test_nested_args_templates_and_lists() -> None:
    traces = [
        mk_trace(
            f"t{i}",
            [
                ("shop.week", {"week": w}, {"start": s}),
                (
                    "shop.search",
                    {"filter": {"since": s, "q": f"week={w}"}, "labels": ["a", "b"], "n": 5},
                    [],
                ),
            ],
            inputs={"week": w},
        )
        for i, (w, s) in enumerate(
            [("2026-W42", "2026-10-12"), ("2026-W43", "2026-10-19"), ("2026-W44", "2026-10-26")]
        )
    ]
    skeleton = build_skeleton(traces)
    gen = generate(skeleton, traces, collect_holes(skeleton, traces), Fills(), version=1)
    line = next(ln for ln in gen.source.splitlines() if "shop.search" in ln)
    assert 'filter={"since": r0["start"], "q": f"week={inputs.week}"}' in line
    assert 'labels=["a", "b"]' in line and "n=5" in line
    ast.parse(gen.source)


def test_conditional_data_predicate_and_gate() -> None:
    def traces(with_separator: bool) -> list[Trace]:
        out: list[Trace] = []
        for i, n in enumerate([0, 5, 9]):
            res: dict[str, Any] = (
                {"count": n}
                if not with_separator
                else {"count": n, "label": "hot" if n > 2 else "cold"}
            )
            calls: list[Any] = [("shop.count", {}, res if with_separator else {"count": 1})]
            if n > 2:
                calls.append(
                    ("slack.post", {"text": "alert: threshold exceeded today"}, {"ok": True})
                )
            calls.append(("shop.done", {}, None))
            out.append(mk_trace(f"t{i}", calls))
        return out

    data = traces(True)
    sk = build_skeleton(data)
    gen = generate(sk, data, collect_holes(sk, data), Fills(), version=1)
    assert "r1 = None" in gen.source
    assert 'if r0["count"] > 0:' in gen.source
    gated = traces(False)
    sk2 = build_skeleton(gated)
    gen2 = generate(sk2, gated, collect_holes(sk2, gated), Fills(), version=1)
    assert (
        'g1 = ctx.llm("gate1", Gate1Out, PROMPT_gate1' in gen2.source and "if g1.go:" in gen2.source
    )
    assert "class Gate1Out(BaseModel)" in gen2.source and "go: bool" in gen2.source
    assert gen2.llm_steps[0].kind == "gate"
    for src in (gen.source, gen2.source):
        ast.parse(src)
        lint_program(src)


def test_helpers() -> None:
    assert path_expr("r0", "$.a[0].b") == 'r0["a"][0]["b"]'
    assert render_prompt("P", "") == 'P = ""'
    rendered = render_prompt("P", 'line one\nline "two"')
    assert rendered.startswith("P = (\n") and '"line one\\n"' in rendered
    assert '"line \\"two\\""' in rendered
    long = render_prompt("P", "word " * 40)
    assert all(len(line) <= 100 for line in long.splitlines())
