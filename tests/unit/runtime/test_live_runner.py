"""Live backend against the echo server; the runner against the weekly-report mock servers
(compiled from the synthetic fixture recordings with the recorded model backend). Offline."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from amber.compile.compiler import compile_workflow
from amber.compile.manifest import Budgets, Manifest, StepSummary, ToolEntry
from amber.compile.versions import get_version
from amber.config import AmberConfig, ServerCfg
from amber.errors import AmberError
from amber.models.recorded import RecordedBackend
from amber.project import init_project
from amber.runtime.backends.live import LiveBackend
from amber.runtime.ledger import Ledger
from amber.runtime.runner import run_program
from amber.trace.models import Trace
from amber.trace.store import TraceStore
from amber.util.json import read_json

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures"
ECHO = FIXTURES / "servers" / "echo_server.py"
SHOP = REPO / "examples" / "weekly-report" / "server_shop.py"
SLACK = REPO / "examples" / "_shared" / "server_slack.py"


def _echo_manifest(*, hash_: str = "", missing: bool = False) -> Manifest:
    tools = [
        ToolEntry(
            canonical="echo.echo",
            server="echo",
            name="echo",
            effect="read",
            schema_hash=hash_,
            input_schema={"x": 1} if hash_ else {},
        )
    ]
    if missing:
        tools.append(ToolEntry(canonical="echo.gone", server="echo", name="gone"))
    return Manifest(
        workflow="echo", version=1, created_at=datetime(2026, 11, 2, tzinfo=UTC), source_traces=[],
        skeleton_hash="x", tools=tools, servers={"echo": [t.name for t in tools]},
        steps=[StepSummary(idx=0, tool="echo.echo", kind="deterministic")],
        budgets=Budgets(max_calls=5, max_llm_tokens=10, wall_clock_s=60),
    )  # fmt: skip


def _echo_servers() -> dict[str, ServerCfg]:
    return {"echo": ServerCfg(command=sys.executable, args=[str(ECHO)])}


async def test_live_backend_lists_calls_and_detects_drift() -> None:
    live = LiveBackend(_echo_manifest(), _echo_servers())
    events = await live.start()
    try:
        assert events == [] and set(live.tools) == {"echo.echo", "echo.shout"}
        assert live.effect("echo.echo") == "read" and live.effect("echo.shout") == "write"
        ok = await live.call("echo.echo", {"text": "hi"})
        assert ok.value == {"text": "hi"} and not ok.is_error and ok.duration_ms >= 0
        err = await live.call("echo.echo", {})
        assert err.is_error and err.error_text
    finally:
        await live.stop()
    drifted = LiveBackend(_echo_manifest(hash_="deadbeefdeadbeef", missing=True), _echo_servers())
    events = await drifted.start()
    await drifted.stop()
    kinds = sorted(e.detail.split(" ")[0] + ":" + e.severity for e in events)
    assert len(events) == 2 and all(e.severity == "hard" for e in events)
    assert any("missing" in e.detail for e in events) and any(
        "schema changed" in e.detail for e in events
    )
    assert kinds


async def test_live_backend_requires_server_config() -> None:
    live = LiveBackend(_echo_manifest(), {})
    with pytest.raises(AmberError, match="not configured"):
        await live.start()


@pytest.fixture
def compiled_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, AmberConfig]:
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
    backend = RecordedBackend(directory=FIXTURES / "model_fills")
    result = asyncio.run(compile_workflow(tmp_path, cfg, traces, backend=backend))
    assert result.usable
    return tmp_path, cfg


def test_run_dry_run_then_live(compiled_project: tuple[Path, AmberConfig]) -> None:
    root, cfg = compiled_project
    info = get_version(root, "weekly-report", usable_only=True)
    llm = RecordedBackend(directory=FIXTURES / "model_fills")
    seen: list[str] = []
    result = run_program(
        root,
        cfg,
        info,
        {"week": "2026-W44"},
        llm_backend=llm,
        on_step=lambda s: seen.append(f"{s.tool}:{s.status}"),
    )
    assert result.status == "ok", result.error
    assert result.steps == 5 and result.llm_calls == 1 and result.agent_turns == 0
    assert seen == [
        "shop.week_range:ok",
        "shop.list_orders:ok",
        "shop.top_products:ok",
        "shop.rfm_snapshot:ok",
        "llm:step4:llm",
        "slack.post_message:dry-run",
    ]
    assert (
        result.output is not None
        and result.output["ok"] is True
        and result.output["ts"] == "<volatile>"
    )
    assert "Week 2026-W44" in result.output["text"]
    assert not (root / ".outbox" / "slack.jsonl").exists()
    assert any(e.guard == "write_effect" for e in result.guard_events)
    ledger = Ledger(root / ".amber" / "amber.db")
    row = ledger.run(result.run_id)
    assert row is not None and row.status == "ok" and row.mode == "dry-run" and row.llm_calls == 1
    assert [s["status"] for s in ledger.steps(result.run_id)] == ["ok", "ok", "ok", "ok", "dry-run"]

    live = run_program(root, cfg, info, {"week": "2026-W44"}, allow_writes=True, llm_backend=llm)
    assert live.status == "ok" and live.mode == "live"
    posted = [
        json.loads(line)
        for line in (root / ".outbox" / "slack.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert posted[-1]["channel"] == "#eng" and posted[-1]["text"].startswith("Week 2026-W44")
    assert live.output is not None and live.output["ts"] != "<volatile>"


def test_run_stops_on_result_shape_drift(compiled_project: tuple[Path, AmberConfig]) -> None:
    root, cfg = compiled_project
    cfg.servers["shop"].env["AMBER_SCENARIO"] = "B"
    info = get_version(root, "weekly-report")
    result = run_program(
        root,
        cfg,
        info,
        {"week": "2026-W44"},
        llm_backend=RecordedBackend(directory=FIXTURES / "model_fills"),
    )
    assert result.status == "stopped" and result.stop_kind == "result_shape"
    assert result.stop_detail is not None and "missing key 'total'" in result.stop_detail
    assert result.steps == 2  # week_range ok, list_orders drifted
    row = Ledger(root / ".amber" / "amber.db").run(result.run_id)
    assert row is not None and row.status == "stopped" and row.stop_kind == "result_shape"


def test_run_rejects_bad_inputs_and_unusable_versions(
    compiled_project: tuple[Path, AmberConfig],
) -> None:
    root, cfg = compiled_project
    info = get_version(root, "weekly-report")
    with pytest.raises(AmberError, match="invalid inputs"):
        run_program(root, cfg, info, {"weekk": "x"})
    manifest = info.manifest()
    assert manifest is not None
    manifest.usable = False
    (info.path / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")), encoding="utf-8"
    )
    with pytest.raises(AmberError, match="not usable"):
        run_program(root, cfg, info, {"week": "2026-W44"})


def test_run_without_llm_backend_stops(compiled_project: tuple[Path, AmberConfig]) -> None:
    root, cfg = compiled_project
    info = get_version(root, "weekly-report")
    result = run_program(root, cfg, info, {"week": "2026-W44"}, llm_backend=None)
    assert result.status == "stopped" and result.stop_kind == "llm_output"


def _unused(_: Any) -> None:  # keeps the Any import meaningful for pyright strictness in tests
    pass
