"""Doctor: decision table, ledger-driven checks, live probe (echo/shop servers), CLI exit 5."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amber.cli.app import app
from amber.compile.compiler import compile_workflow
from amber.compile.versions import get_version
from amber.config import AmberConfig
from amber.doctor.checks import Finding
from amber.doctor.diagnosis import decide, diagnose
from amber.models.recorded import RecordedBackend
from amber.project import init_project
from amber.runtime.ledger import Ledger
from amber.runtime.runner import run_program
from amber.trace.models import Trace
from amber.trace.store import TraceStore
from amber.util.json import read_json

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures"
SHOP = REPO / "examples" / "weekly-report" / "server_shop.py"
SLACK = REPO / "examples" / "_shared" / "server_slack.py"
runner = CliRunner()


def F(check: str, severity: str) -> Finding:  # tiny helper
    return Finding(check, severity, f"{check} detail")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("findings", "expected"),
    [
        ([F("servers", "ok"), F("schema_drift", "ok")], ("healthy", "none")),
        ([F("staleness", "soft")], ("healthy", "recompile")),
        ([F("llm_health", "soft")], ("degraded", "none")),
        ([F("schema_drift", "soft")], ("degraded", "recompile")),
        ([F("result_shape", "soft"), F("staleness", "soft")], ("degraded", "recompile")),
        ([F("servers", "ok"), F("result_shape", "hard")], ("degraded", "fallback")),
        ([F("schema_drift", "hard"), F("staleness", "soft")], ("degraded", "fallback")),
        ([F("guard_stats", "hard")], ("degraded", "fallback")),
        ([F("servers", "hard"), F("result_shape", "hard")], ("broken", "re-record")),
        ([F("program_lint", "hard"), F("schema_drift", "hard")], ("broken", "recompile")),
    ],
)
def test_decision_table(findings: list[Finding], expected: tuple[str, str]) -> None:
    status, recommendation, rationale = decide(findings)
    assert (status, recommendation) == expected and rationale


@pytest.fixture
def project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, AmberConfig, TraceStore]:
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
    result = asyncio.run(
        compile_workflow(
            tmp_path, cfg, traces, backend=RecordedBackend(directory=FIXTURES / "model_fills")
        )
    )
    assert result.usable
    return tmp_path, cfg, store


def test_healthy_then_stale_then_lint_broken(project: tuple[Path, AmberConfig, TraceStore]) -> None:
    root, cfg, store = project
    info = get_version(root, "weekly-report")
    ledger = Ledger(root / ".amber" / "amber.db")
    d = diagnose(root, cfg, info, store=store, ledger=ledger, probe_servers=False)
    assert d.status == "healthy" and d.recommendation == "none"
    assert {f.check for f in d.findings} == {
        "result_shape",
        "guard_stats",
        "llm_health",
        "staleness",
        "program_lint",
    }
    # a newer trace (e.g. a fallback) that the program does not cover
    t3 = store.load("t3")
    manifest = info.manifest()
    assert manifest is not None
    store.save(
        t3.model_copy(
            update={
                "id": "t_new",
                "created_at": manifest.created_at + timedelta(hours=1),
                "origin": "fallback",
            }
        )
    )
    stale = diagnose(root, cfg, info, store=store, ledger=ledger, probe_servers=False)
    assert (
        stale.status == "healthy"
        and stale.recommendation == "recompile"
        and "t_new" in stale.rationale
    )
    # editing program.py after compile breaks the hash
    program = info.program_path
    program.write_text(program.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    broken = diagnose(root, cfg, info, store=store, ledger=ledger, probe_servers=False)
    assert broken.status == "broken" and broken.recommendation == "recompile"
    assert "edited after compile" in next(
        f.detail for f in broken.findings if f.check == "program_lint"
    )


def test_degraded_after_drifted_runs_with_live_probe(
    project: tuple[Path, AmberConfig, TraceStore],
) -> None:
    root, cfg, store = project
    info = get_version(root, "weekly-report")
    ledger = Ledger(root / ".amber" / "amber.db")
    llm = RecordedBackend(directory=FIXTURES / "model_fills")
    cfg.servers["shop"].env["AMBER_SCENARIO"] = "B"
    for _ in range(2):
        stopped = run_program(root, cfg, info, {"week": "2026-W44"}, llm_backend=llm)
        assert stopped.status == "stopped"
    d = diagnose(root, cfg, info, store=store, ledger=ledger, probe_servers=True)
    assert d.status == "degraded" and d.recommendation == "fallback"
    by = {f.check: f for f in d.findings}
    assert by["servers"].severity == "ok" and by["schema_drift"].severity == "ok"
    assert by["result_shape"].severity == "hard" and "total" in by["result_shape"].detail
    assert by["guard_stats"].severity == "hard" and "2/2" in by["guard_stats"].detail
    assert "amber run weekly-report --fallback agent" in d.next_command()
    assert json.dumps(d.to_json())


def test_unreachable_server_is_broken(project: tuple[Path, AmberConfig, TraceStore]) -> None:
    root, cfg, store = project
    cfg.servers["shop"].command = str(root / "does-not-exist.exe")
    d = diagnose(
        root,
        cfg,
        get_version(root, "weekly-report"),
        store=store,
        ledger=Ledger(root / ".amber" / "amber.db"),
    )
    assert d.status == "broken" and d.recommendation == "re-record"
    assert next(f for f in d.findings if f.check == "servers").severity == "hard"


def test_doctor_cli(
    project: tuple[Path, AmberConfig, TraceStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _, _ = project
    py = json.dumps(sys.executable)
    shop, slack = json.dumps(str(SHOP)), json.dumps(str(SLACK))
    (root / "amber.toml").write_text(
        f'[project]\nname = "wr"\n\n[servers.shop]\ncommand = {py}\nargs = [{shop}]\n'
        f'env = {{ AMBER_SCENARIO = "B" }}\n\n[servers.slack]\ncommand = {py}\nargs = [{slack}]\n',
        encoding="utf-8",
    )
    ok = runner.invoke(app, ["doctor", "weekly-report", "--no-probe", "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["status"] == "healthy"
    monkeypatch.setenv("AMBER_MODEL_BACKEND", "recorded")
    monkeypatch.setenv("AMBER_MODEL_FILLS", str(FIXTURES / "model_fills"))
    stopped = runner.invoke(app, ["run", "weekly-report", "--input", "week=2026-W44"])
    assert stopped.exit_code == 4, stopped.output
    bad = runner.invoke(app, ["doctor", "weekly-report", "--json"])
    assert bad.exit_code == 5, bad.output
    data = json.loads(bad.stdout)
    assert data["status"] != "healthy" and data["recommendation"] in ("fallback", "recompile")
    human = runner.invoke(app, ["doctor", "weekly-report", "--no-probe"])
    assert (
        human.exit_code == 5 and "Recommendation" in human.stdout and "result_shape" in human.stdout
    )
    shutil.rmtree(root / "programs")
    assert runner.invoke(app, ["doctor", "weekly-report"]).exit_code != 0


def _ledger_run(
    ledger: Ledger, version: int, status: str, events: list[tuple[str, str]] = ()
) -> str:
    run_id = ledger.open_run("wf", version, "live", {})
    for severity, detail in events:
        ledger.add_guard_event(run_id, 0, "result_shape", severity, detail)
    ledger.close_run(
        run_id, status=status, steps=1, llm_calls=0, llm_input_tokens=0, llm_output_tokens=0,
        cost_usd=None,
    )  # fmt: skip
    return run_id


def test_doctor_statistics_cover_only_the_diagnosed_version(tmp_path: Path) -> None:
    """After `compile --refresh` the stops of v1 must not keep v2 degraded."""
    from amber.doctor.checks import check_guard_stats, check_result_shape

    ledger = Ledger(tmp_path / "amber.db")
    for _ in range(3):
        _ledger_run(ledger, 1, "stopped", [("hard", "$.total: missing key")])
    _ledger_run(ledger, 2, "ok")
    assert check_guard_stats(ledger, "wf", 10, version=1).severity == "hard"
    assert check_guard_stats(ledger, "wf", 10, version=2).severity == "ok"
    assert check_result_shape(ledger, "wf", 10, version=2).severity == "ok"
    assert len(ledger.recent_runs("wf", 10)) == 4  # unscoped callers still see everything


def test_result_shape_detail_comes_from_the_hard_event(tmp_path: Path) -> None:
    from amber.doctor.checks import check_result_shape

    ledger = Ledger(tmp_path / "amber.db")
    _ledger_run(ledger, 1, "stopped", [("hard", "$: type array, recorded object")])
    _ledger_run(ledger, 1, "ok", [("soft", "$: empty list where recordings had items")])
    finding = check_result_shape(ledger, "wf", 10, version=1)
    assert finding.severity == "hard" and finding.detail.startswith("$: type array")
