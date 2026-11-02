"""`amber traces ls|show|rm` against a project seeded with the fixture traces."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amber.cli.app import app
from amber.project import init_project

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "traces" / "weekly-report"
runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    init_project(tmp_path)
    dest = tmp_path / ".amber" / "traces" / "weekly-report"
    dest.mkdir(parents=True)
    for name in ("t1.json", "t2.json", "t3.json"):
        shutil.copy(FIXTURES / name, dest / name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_ls_json_and_table(project: Path) -> None:
    r = runner.invoke(app, ["traces", "ls", "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert [row["id"] for row in rows] == ["t1", "t2", "t3"]
    assert rows[0]["workflow"] == "weekly-report" and rows[1]["calls"] == 6
    human = runner.invoke(app, ["traces", "ls", "weekly-report"])
    assert human.exit_code == 0 and "t2" in human.stdout and "Traces" in human.stdout
    empty = runner.invoke(app, ["traces", "ls", "nothing", "--json"])
    assert json.loads(empty.stdout) == []


def test_show_table_full_and_json(project: Path) -> None:
    r = runner.invoke(app, ["traces", "show", "t2"])
    assert r.exit_code == 0, r.output
    assert "builtin.Read" in r.stdout and "outcome: success" in r.stdout
    full = runner.invoke(app, ["traces", "show", "t2", "--full"])
    data = json.loads(full.stdout)
    assert data["id"] == "t2" and data["schema_version"] == 1
    assert json.loads(runner.invoke(app, ["traces", "show", "t1", "--json"]).stdout)["inputs"] == {
        "week": "2026-W42"
    }


def test_show_missing_trace_errors(project: Path) -> None:
    r = runner.invoke(app, ["traces", "show", "nope"])
    assert r.exit_code != 0


def test_rm(project: Path) -> None:
    r = runner.invoke(app, ["traces", "rm", "t1", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == {"deleted": "t1"}
    assert not (project / ".amber" / "traces" / "weekly-report" / "t1.json").exists()
    assert runner.invoke(app, ["traces", "rm", "t1"]).exit_code == 2


def test_commands_need_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["traces", "ls"])
    assert r.exit_code != 0
    assert r.exception is not None and "amber init" in str(getattr(r.exception, "hint", ""))
