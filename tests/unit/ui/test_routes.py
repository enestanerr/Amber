"""Panel routes return 200 with a fixture DB (compiled program + one fake run); the JSON twins
validate against the Pydantic models; chart functions return well-formed SVG."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amber.compile.compiler import compile_workflow
from amber.config import AmberConfig
from amber.models.recorded import RecordedBackend
from amber.project import init_project
from amber.runtime.ledger import Ledger
from amber.trace.models import Trace
from amber.trace.store import TraceStore
from amber.ui.app import create_app
from amber.ui.charts import GuardRow, TokenRow, guard_events_svg, tokens_per_run_svg
from amber.ui.data import Panel
from amber.ui.models import DecisionRow, OverviewRow, ProgramRow, RunDetail, RunSummary
from amber.util.json import read_json

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, str]]:
    init_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    store = TraceStore(tmp_path / ".amber")
    traces = [
        Trace.model_validate(read_json(FIXTURES / "traces" / "weekly-report" / f"t{i}.json"))
        for i in (1, 2, 3)
    ]
    for t in traces:
        store.save(t)
    cfg = AmberConfig()
    result = asyncio.run(
        compile_workflow(
            tmp_path, cfg, traces, backend=RecordedBackend(directory=FIXTURES / "model_fills")
        )
    )
    assert result.usable
    ledger = Ledger(tmp_path / ".amber" / "amber.db")
    run_id = ledger.open_run("weekly-report", 1, "dry-run", {"week": "2026-W44"})
    ledger.add_step(
        run_id,
        0,
        kind="tool",
        tool="shop.week_range",
        args={"week": "2026-W44"},
        result_digest="abc",
        duration_ms=40,
        status="ok",
    )
    ledger.add_step(
        run_id,
        4,
        kind="tool",
        tool="slack.post_message",
        args={"channel": "#eng"},
        result_digest="",
        duration_ms=0,
        status="dry-run",
    )
    ledger.add_guard_event(run_id, 4, "write_effect", "info", "dry-run")
    ledger.add_guard_event(run_id, 1, "result_shape", "soft", "$[0]: extra key 'x'")
    ledger.add_llm_call(
        run_id,
        4,
        "step4",
        "claude-haiku-5-5",
        input_tokens=1500,
        output_tokens=90,
        valid=True,
        confidence=0.86,
    )
    ledger.add_decision(
        run_id,
        "weekly-report",
        proposal={"proposed_action": "fallback_resume", "step_idx": 1, "tool": "shop.list_orders"},
        vetoes=[{"rule": "writes_not_allowed", "reason": "x", "blocks": ["fallback_resume"]}],
        decision="abort",
        reason="vetoed",
        actor="arbiter",
    )
    ledger.close_run(
        run_id,
        status="ok",
        steps=5,
        llm_calls=1,
        llm_input_tokens=1500,
        llm_output_tokens=90,
        cost_usd=None,
    )
    app = create_app(tmp_path, panel=Panel(tmp_path, cfg=cfg))
    with TestClient(app) as c:
        yield c, run_id


@pytest.mark.parametrize(
    "path",
    ["/", "/programs", "/programs/weekly-report", "/programs/weekly-report/v1", "/runs",
     "/runs?workflow=weekly-report",
     "/doctor/weekly-report", "/decisions", "/decisions?workflow=weekly-report", "/traces/t1"],
)  # fmt: skip
def test_pages_render(client: tuple[TestClient, str], path: str) -> None:
    c, _ = client
    r = c.get(path)
    assert r.status_code == 200, (path, r.text[:200])
    assert "weekly-report" in r.text and "<title>" in r.text
    assert "cdn" not in r.text.lower()


def test_run_page_and_404s(client: tuple[TestClient, str]) -> None:
    c, run_id = client
    r = c.get(f"/runs/{run_id}")
    assert (
        r.status_code == 200
        and "shop.week_range" in r.text
        and "result_shape" in r.text
        and "abort" in r.text
    )
    assert c.get("/runs/nope").status_code == 404
    assert c.get("/programs/nothing").status_code == 404
    assert c.get("/programs/weekly-report/v9").status_code == 404
    assert c.get("/traces/nope").status_code == 404
    assert (
        c.get("/static/htmx.min.js").status_code == 200
        and c.get("/static/app.css").status_code == 200
    )


def test_api_twins_validate(client: tuple[TestClient, str]) -> None:
    c, run_id = client
    runs = [RunSummary.model_validate(r) for r in c.get("/api/runs").json()]
    assert runs and runs[0].id == run_id and runs[0].llm_input_tokens == 1500
    detail = RunDetail.model_validate(c.get(f"/api/runs/{run_id}").json())
    assert len(detail.steps) == 2 and detail.guard_events and detail.llm_calls and detail.decisions
    overview = [OverviewRow.model_validate(r) for r in c.get("/api/overview").json()]
    assert (
        overview[0].workflow == "weekly-report" and overview[0].usable and overview[0].runs_7d == 1
    )
    assert overview[0].agent_median_tokens > 0 and overview[0].tokens_saved > 0
    programs = [ProgramRow.model_validate(r) for r in c.get("/api/programs").json()]
    assert (
        programs[0].version == 1
        and programs[0].judgment_steps == 1
        and programs[0].transform_steps == 1
    )
    decisions = [DecisionRow.model_validate(r) for r in c.get("/api/decisions").json()]
    assert (
        decisions[0].decision == "abort" and decisions[0].vetoes[0]["rule"] == "writes_not_allowed"
    )
    assert c.get("/api/doctor/weekly-report").json() is None
    assert c.get("/api/traces/t2").json()["id"] == "t2"
    assert c.get("/api/runs/nope").status_code == 404


def test_doctor_post_returns_fragment(client: tuple[TestClient, str]) -> None:
    c, _ = client
    r = c.post("/doctor/weekly-report?probe=0")
    assert r.status_code == 200 and "Recommendation" in r.text and "program_lint" in r.text
    assert c.get("/api/doctor/weekly-report").json()["status"] in ("healthy", "degraded")
    assert "Recommendation" in c.get("/doctor/weekly-report").text  # cached diagnosis shown
    missing = c.post("/doctor/nothing?probe=0")
    assert missing.status_code == 200 and "error" in missing.text


def test_charts_are_valid_svg() -> None:
    svg = tokens_per_run_svg(
        [TokenRow("r1", 18000, 1600), TokenRow("r2", 18000, 1700)], baseline=18000
    )
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and root.find("{http://www.w3.org/2000/svg}title") is not None
    assert "agent baseline" in svg and 'class="s2"' in svg and "stroke-dasharray" in svg
    guard = guard_events_svg([GuardRow("2026-11-01", 2, 1), GuardRow("2026-11-02", 0, 3)])
    ET.fromstring(guard)
    assert 'class="s4"' in guard and 'class="s2"' in guard
    ET.fromstring(tokens_per_run_svg([]))
    ET.fromstring(guard_events_svg([]))
    assert json.dumps({"ok": True})
