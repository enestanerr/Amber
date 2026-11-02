"""`amber align` on a project seeded with the weekly-report fixtures."""

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


def test_align_show_prints_table_and_writes_skeleton(project: Path) -> None:
    r = runner.invoke(app, ["align", "weekly-report", "--show"])
    assert r.exit_code == 0, r.output
    assert "5 steps" in r.stdout and "1 judgment" in r.stdout and "1 transform-needed" in r.stdout
    assert "1 pruned" in r.stdout and "(pruned)" in r.stdout
    assert "Input(week)" in r.stdout and "Ref(0,$.start)" in r.stdout
    assert (project / ".amber" / "build" / "weekly-report" / "skeleton.json").is_file()


def test_align_json(project: Path) -> None:
    r = runner.invoke(app, ["align", "weekly-report", "--json"])
    assert r.exit_code == 0, r.output
    s = json.loads(r.stdout)
    assert s["steps"] and s["inputs"]
    assert len(s["steps"]) == 5 and len(s["pruned"]) == 1 and s["hash"]


def test_align_keep_unreferenced(project: Path) -> None:
    r = runner.invoke(app, ["align", "weekly-report", "--keep-unreferenced", "--json"])
    s = json.loads(r.stdout)
    assert len(s["steps"]) == 6 and s["pruned"] == []


def test_align_without_traces_fails(project: Path) -> None:
    r = runner.invoke(app, ["align", "nothing-here"])
    assert r.exit_code != 0


def test_align_skips_failed_traces_unless_included(project: Path) -> None:
    path = project / ".amber" / "traces" / "weekly-report" / "t3.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["outcome"]["status"] = "failure"
    path.write_text(json.dumps(data), encoding="utf-8")
    default = json.loads(runner.invoke(app, ["align", "weekly-report", "--json"]).stdout)
    assert default["traces"] == ["t1", "t2"]
    included = json.loads(
        runner.invoke(app, ["align", "weekly-report", "--json", "--include-failed"]).stdout
    )
    assert included["traces"] == ["t1", "t2", "t3"]
