"""`amber run` against the weekly-report mock servers (offline, recorded model backend)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amber.cli.app import app
from amber.project import init_project

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures"
SHOP = REPO / "examples" / "weekly-report" / "server_shop.py"
SLACK = REPO / "examples" / "_shared" / "server_slack.py"
runner = CliRunner()


def _toml(scenario: str = "A") -> str:
    py = json.dumps(sys.executable)
    return (
        '[project]\nname = "wr"\n\n[models]\nbackend = "recorded"\n\n'
        f"[servers.shop]\ncommand = {py}\nargs = [{json.dumps(str(SHOP))}]\n"
        f'env = {{ AMBER_SCENARIO = "{scenario}" }}\n\n'
        f"[servers.slack]\ncommand = {py}\nargs = [{json.dumps(str(SLACK))}]\n"
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    init_project(tmp_path)
    (tmp_path / "amber.toml").write_text(_toml(), encoding="utf-8")
    dest = tmp_path / ".amber" / "traces" / "weekly-report"
    dest.mkdir(parents=True)
    for name in ("t1.json", "t2.json", "t3.json"):
        shutil.copy(FIXTURES / "traces" / "weekly-report" / name, dest / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMBER_MODEL_FILLS", str(FIXTURES / "model_fills"))
    compiled = runner.invoke(app, ["compile", "weekly-report", "--json"])
    assert compiled.exit_code == 0, compiled.output
    return tmp_path


def test_run_dry_run_and_allow_writes(project: Path) -> None:
    r = runner.invoke(
        app, ["run", "weekly-report", "--input", "week=2026-W44", "--dry-run", "--json"]
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["status"] == "ok" and data["agent_turns"] == 0 and data["llm_calls"] <= 2
    assert [s["status"] for s in data["step_log"]] == ["ok", "ok", "ok", "ok", "llm", "dry-run"]
    human = runner.invoke(
        app, ["run", "weekly-report", "--input", "week=2026-W44", "--allow-writes"]
    )
    assert human.exit_code == 0, human.output
    assert "→ ok" in human.stdout and "agent turns 0" in human.stdout
    assert (project / ".outbox" / "slack.jsonl").exists()


def test_run_guard_stop_exits_4(project: Path) -> None:
    (project / "amber.toml").write_text(_toml("B"), encoding="utf-8")
    r = runner.invoke(app, ["run", "weekly-report", "--input", "week=2026-W44", "--json"])
    assert r.exit_code == 4, r.output
    data = json.loads(r.stdout)
    assert data["status"] == "stopped" and data["stop_kind"] == "result_shape"


def test_run_usage_errors(project: Path) -> None:
    assert (
        runner.invoke(app, ["run", "weekly-report", "--dry-run", "--allow-writes"]).exit_code != 0
    )
    assert runner.invoke(app, ["run", "weekly-report", "--fallback", "maybe"]).exit_code != 0
    assert runner.invoke(app, ["run", "weekly-report"]).exit_code != 0  # missing input
