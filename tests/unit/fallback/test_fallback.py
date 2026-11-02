"""Fallback: proposal, sentry rules, arbiter table, resume prompt golden, splice, pipeline
end-to-end with a fake `claude`, recompile with the resumed trace."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from amber.cli._output import Output
from amber.compile.compiler import compile_workflow
from amber.compile.manifest import Budgets, Manifest, StepSummary
from amber.compile.versions import get_version
from amber.config import AmberConfig
from amber.fallback.agent_fallback import record_fallback, render_resume_prompt, splice_prefix
from amber.fallback.arbiter import arbitrate
from amber.fallback.pipeline import decide_and_act
from amber.fallback.proposal import build_proposal
from amber.fallback.sentry import SentryContext, evaluate
from amber.models.recorded import RecordedBackend
from amber.project import init_project
from amber.runtime.ledger import Ledger
from amber.runtime.runner import RunResult, StepLog, run_program
from amber.trace.models import ToolCall, Trace
from amber.trace.store import TraceStore
from amber.util.json import read_json
from amber.util.time import utcnow
from tests.conftest import install_fake_claude

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures"
SHOP = REPO / "examples" / "weekly-report" / "server_shop.py"
SLACK = REPO / "examples" / "_shared" / "server_slack.py"
GOLDEN = Path(__file__).parent / "golden"


def _manifest() -> Manifest:
    return Manifest(
        workflow="wf", version=1, created_at=utcnow(), source_traces=["t1"], skeleton_hash="x",
        steps=[
            StepSummary(idx=0, tool="shop.week", kind="deterministic", effect="read"),
            StepSummary(idx=1, tool="shop.list", kind="deterministic", effect="read"),
            StepSummary(idx=2, tool="slack.post", kind="judgment", effect="write"),
        ],
        budgets=Budgets(max_calls=6, max_llm_tokens=100, wall_clock_s=60),
    )  # fmt: skip


def _result(stop_kind: str, *, stopped_at: int = 1, completed: int = 1) -> RunResult:
    log = [StepLog(i, f"step{i}", "ok", 1) for i in range(completed)]
    log.append(StepLog(stopped_at, "shop.list", "stopped", 2, "missing key"))
    return RunResult(
        run_id="r_x", workflow="wf", version=1, status="stopped", mode="dry-run",
        steps=completed + 1,
        llm_calls=0, llm_input_tokens=0, llm_output_tokens=0, cost_usd=None, duration_ms=5,
        stop_kind=stop_kind, stop_detail="missing key 'total'", step_log=log,
    )  # fmt: skip


def test_build_proposal() -> None:
    p = build_proposal(_result("result_shape"), _manifest())
    assert p.step_idx == 1 and p.tool == "shop.list" and p.proposed_action == "fallback_resume"
    assert (
        p.completed_steps == [0]
        and p.completed_write_steps == []
        and p.remaining_write_steps == [2]
    )
    assert build_proposal(_result("error"), _manifest()).proposed_action == "retry"
    assert (
        build_proposal(_result("error"), _manifest(), retried=True).proposed_action
        == "fallback_resume"
    )
    assert build_proposal(_result("budget"), _manifest()).proposed_action == "abort"


def _ctx(**kw: Any) -> SentryContext:
    base: dict[str, Any] = {
        "fallback_enabled": True,
        "allow_writes": True,
        "daily_budget": 3,
        "fallbacks_today": 0,
    }
    base.update(kw)
    return SentryContext(**base)


def test_sentry_rules() -> None:
    p = build_proposal(_result("result_shape"), _manifest())
    assert evaluate(p, _ctx()) == []
    assert [v.rule for v in evaluate(p, _ctx(fallback_enabled=False))] == ["no_fallback_flag"]
    assert [v.rule for v in evaluate(p, _ctx(fallbacks_today=3))] == ["daily_budget"]
    assert [v.rule for v in evaluate(p, _ctx(allow_writes=False))] == ["writes_not_allowed"]
    done = p.model_copy(update={"completed_write_steps": [0]})
    assert evaluate(done, _ctx())[0].rule == "writes_already_done" and evaluate(done, _ctx())[
        0
    ].blocks == ["fallback_full"]
    budget = build_proposal(_result("budget"), _manifest())
    assert [v.rule for v in evaluate(budget, _ctx())] == ["budget_exhausted"]
    unreachable = p.model_copy(update={"stop_detail": "server unreachable"})
    assert "servers_unreachable" in [v.rule for v in evaluate(unreachable, _ctx())]
    assert "lint_failure" in [v.rule for v in evaluate(p, _ctx(lint_failed=True))]


def test_arbiter_table() -> None:
    m = _manifest()
    assert arbitrate(build_proposal(_result("error"), m), []).action == "retry"
    assert arbitrate(build_proposal(_result("result_shape"), m), []).action == "fallback_resume"
    assert arbitrate(build_proposal(_result("schema_drift"), m), []).action == "fallback_resume"
    assert arbitrate(build_proposal(_result("budget"), m), []).action == "abort"
    p = build_proposal(_result("result_shape"), m)
    vetoed = arbitrate(p, evaluate(p, _ctx(fallback_enabled=False)))
    assert vetoed.action == "abort" and "no_fallback_flag" in vetoed.reason
    full = p.model_copy(update={"proposed_action": "fallback_full"})
    assert arbitrate(full, []).action == "fallback_resume"  # v0.1 never re-runs from scratch
    retry_vetoed = arbitrate(
        build_proposal(_result("error"), m), evaluate(build_proposal(_result("budget"), m), _ctx())
    )
    assert retry_vetoed.action == "abort"


def test_resume_prompt_golden(update_golden: bool) -> None:
    completed = [
        ToolCall(
            idx=0,
            call_id="c0",
            tool="shop.week_range",
            args={"week": "2026-W44"},
            result={"start": "2026-10-26", "end": "2026-11-01"},
            result_kind="json",
        )
    ]
    prompt = render_resume_prompt(
        workflow="weekly-report", inputs={"week": "2026-W44"}, completed=completed, step_idx=1,
        tool="shop.list_orders", stop_detail="result shape: missing key 'total'",
        task_prompt="Pull the orders for week 2026-W44 and post a summary.", allow_writes=False,
    )  # fmt: skip
    golden = GOLDEN / "resume_prompt.md"
    if update_golden or not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(prompt, encoding="utf-8", newline="\n")
    assert prompt == golden.read_text(encoding="utf-8")
    assert (
        "Step 2 `shop.list_orders` could not be completed" in prompt
        and "Do not perform any action that writes" in prompt
    )


def test_splice_prefix() -> None:
    t3 = Trace.model_validate(read_json(FIXTURES / "traces" / "weekly-report" / "t3.json"))
    prefix = [t3.calls[0].model_copy(update={"call_id": "run_0"})]
    spliced = splice_prefix(t3, prefix, resumed_from_step=1)
    assert (
        spliced.origin == "fallback"
        and spliced.resumed_from_step == 1
        and len(spliced.prefix_calls) == 1
    )
    assert spliced.events[0].kind == "note" and spliced.calls[0].call_id == "prefix_0_run_0"
    assert [e.idx for e in spliced.events] == list(range(len(spliced.events)))
    spliced.check()


@pytest.fixture
def drifted_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, AmberConfig, TraceStore, RunResult]:
    init_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    store = TraceStore(tmp_path / ".amber")
    traces = [
        Trace.model_validate(read_json(FIXTURES / "traces" / "weekly-report" / f"t{i}.json"))
        for i in (1, 2, 3)
    ]
    for t in traces:
        store.save(t)
    cfg = AmberConfig.model_validate(
        {
            "servers": {
                "shop": {"command": sys.executable, "args": [str(SHOP)]},
                "slack": {"command": sys.executable, "args": [str(SLACK)]},
            }
        }
    )
    llm = RecordedBackend(directory=FIXTURES / "model_fills")
    assert asyncio.run(compile_workflow(tmp_path, cfg, traces, backend=llm)).usable
    cfg.servers["shop"].env["AMBER_SCENARIO"] = "B"
    info = get_version(tmp_path, "weekly-report")
    result = run_program(
        tmp_path, cfg, info, {"week": "2026-W44"}, llm_backend=llm, allow_writes=True
    )
    assert (
        result.status == "stopped"
        and result.stop_kind == "result_shape"
        and len(result.completed_calls) == 1
    )
    # the fake agent resumes from list_orders: replay run3 without its week_range call
    lines = (
        (FIXTURES / "claude_stream" / "weekly_report_run3.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    resumed = [ln for ln in lines if "toolu_01" not in ln]
    fixture = tmp_path / "resumed.jsonl"
    fixture.write_text("\n".join(resumed) + "\n", encoding="utf-8")
    install_fake_claude(tmp_path / "fake-bin", monkeypatch, fixture)
    return tmp_path, cfg, store, result


def test_pipeline_resumes_with_the_agent_and_recompiles(
    drifted_project: tuple[Path, AmberConfig, TraceStore, RunResult],
) -> None:
    root, cfg, store, stopped = drifted_project
    info = get_version(root, "weekly-report")
    ledger = Ledger(root / ".amber" / "amber.db")
    prompts: list[str] = []

    async def capturing(request: Any, **kw: Any) -> Any:
        prompts.append(request.agent_command[2])  # cmd.exe would mangle the multi-line prompt
        return await record_fallback(request, **kw)

    outcome = decide_and_act(
        root, cfg, info, stopped, inputs={"week": "2026-W44"}, allow_writes=True,
        out=Output(json_mode=True), llm_backend=None, recorder=capturing,
        ledger=ledger, store=store,
    )  # fmt: skip
    assert outcome.decision.action == "fallback_resume" and outcome.vetoes == []
    assert outcome.result.status == "ok" and outcome.result.agent_turns > 0 and outcome.trace_id
    trace = store.load(outcome.trace_id)
    assert (
        trace.origin == "fallback"
        and trace.resumed_from_step == 1
        and trace.inputs == {"week": "2026-W44"}
    )
    assert [c.tool for c in trace.calls] == [
        "shop.week_range",
        "shop.list_orders",
        "shop.top_products",
        "shop.rfm_snapshot",
        "slack.post_message",
    ]
    assert trace.calls[0].call_id.startswith("prefix_") and "fallback" in trace.tags
    decisions = ledger.decisions("weekly-report")
    assert decisions and decisions[0]["decision"] == "fallback_resume"
    assert ledger.run(stopped.run_id).agent_turns == outcome.result.agent_turns  # type: ignore[union-attr]
    assert "resuming an automated workflow" in prompts[0]
    assert "Step 2 `shop.list_orders` could not be completed" in prompts[0]
    assert "shop.week_range" in prompts[0]  # the completed prefix is listed
    # recompile with all four traces → v2 verifies against the resumed trace too
    cfg.servers["shop"].env.pop("AMBER_SCENARIO")
    all_traces = store.load_all("weekly-report")
    assert len(all_traces) == 4
    v2 = asyncio.run(
        compile_workflow(
            root,
            cfg,
            all_traces,
            backend=RecordedBackend(directory=FIXTURES / "model_fills"),
            refresh=True,
        )
    )
    assert v2.verify is not None, v2.warnings
    assert v2.version == 2 and v2.usable, [r.summary() for r in v2.verify.reports] + v2.warnings
    assert [r.status for r in v2.verify.reports] == ["pass"] * 4


def test_pipeline_aborts_when_vetoed(
    drifted_project: tuple[Path, AmberConfig, TraceStore, RunResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, cfg, store, stopped = drifted_project
    info = get_version(root, "weekly-report")
    ledger = Ledger(root / ".amber" / "amber.db")

    async def never(*_: Any, **__: Any) -> Any:
        raise AssertionError("the agent must not be launched")

    outcome = decide_and_act(
        root, cfg, info, stopped, inputs={"week": "2026-W44"}, allow_writes=False,
        out=Output(json_mode=True),
        llm_backend=None, recorder=never, ledger=ledger, store=store,
    )  # fmt: skip
    assert outcome.decision.action == "abort" and outcome.vetoes[0].rule == "writes_not_allowed"
    assert (
        outcome.result.status == "stopped"
        and ledger.decisions("weekly-report")[0]["decision"] == "abort"
    )
    assert isinstance(json.loads(ledger.decisions("weekly-report")[0]["vetoes"]), list)


def test_cli_run_with_fallback_agent(
    drifted_project: tuple[Path, AmberConfig, TraceStore, RunResult],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from amber.cli.app import app

    root, _, store, _ = drifted_project
    py = json.dumps(sys.executable)
    shop, slack = json.dumps(str(SHOP)), json.dumps(str(SLACK))
    (root / "amber.toml").write_text(
        f'[project]\nname = "wr"\n\n[models]\nbackend = "recorded"\n\n[servers.shop]\n'
        f'command = {py}\nargs = [{shop}]\nenv = {{ AMBER_SCENARIO = "B" }}\n\n'
        f"[servers.slack]\ncommand = {py}\nargs = [{slack}]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AMBER_MODEL_FILLS", str(FIXTURES / "model_fills"))
    r = CliRunner().invoke(
        app,
        [
            "run",
            "weekly-report",
            "--input",
            "week=2026-W44",
            "--allow-writes",
            "--fallback",
            "agent",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert (
        data["status"] == "ok" and data["agent_turns"] > 0 and data["stop_kind"] == "result_shape"
    )
    assert len(store.list("weekly-report")) == 4
