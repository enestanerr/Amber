from __future__ import annotations

from pathlib import Path

from amber.runtime.ledger import Ledger


def test_ledger_roundtrip(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "amber.db")
    run_id = ledger.open_run("wf", 1, "dry-run", {"week": "2026-W44"})
    assert run_id.startswith("r_")
    row = ledger.run(run_id)
    assert row is not None and row.status == "running" and row.inputs == {"week": "2026-W44"}
    ledger.add_step(
        run_id,
        0,
        kind="tool",
        tool="shop.list",
        args={"a": 1},
        result_digest="abc",
        duration_ms=12,
        status="ok",
    )
    ledger.add_step(
        run_id,
        1,
        kind="tool",
        tool="slack.post",
        args={},
        result_digest="",
        duration_ms=0,
        status="dry-run",
    )
    ledger.add_guard_event(run_id, 1, "write_effect", "info", "dry-run")
    ledger.add_llm_call(
        run_id,
        1,
        "step1",
        "claude-haiku-5-5",
        input_tokens=100,
        output_tokens=20,
        valid=True,
        confidence=0.9,
    )
    ledger.add_decision(
        run_id,
        "wf",
        proposal={"a": 1},
        vetoes=[],
        decision="abort",
        reason="budget",
        actor="arbiter",
    )
    ledger.close_run(
        run_id,
        status="ok",
        steps=2,
        llm_calls=1,
        llm_input_tokens=100,
        llm_output_tokens=20,
        cost_usd=None,
    )
    row = ledger.run(run_id)
    assert row is not None and row.status == "ok" and row.steps == 2 and row.finished_at
    assert [s["tool"] for s in ledger.steps(run_id)] == ["shop.list", "slack.post"]
    assert ledger.guard_events(run_id)[0]["guard"] == "write_effect"
    assert ledger.guard_events(workflow="wf")[0]["severity"] == "info"
    assert ledger.llm_calls(run_id)[0]["confidence"] == 0.9 and ledger.llm_calls(workflow="wf")
    assert ledger.decisions("wf")[0]["decision"] == "abort"
    other = ledger.open_run("other", 2, "live", {})
    ledger.close_run(
        other,
        status="stopped",
        steps=0,
        llm_calls=0,
        llm_input_tokens=0,
        llm_output_tokens=0,
        cost_usd=None,
        stop_kind="budget",
        stop_detail="x",
    )
    assert [r.workflow for r in ledger.recent_runs()] == ["other", "wf"]
    assert [r.id for r in ledger.recent_runs("wf")] == [run_id]
    assert ledger.recent_runs("other")[0].stop_kind == "budget"
    assert ledger.run("nope") is None
