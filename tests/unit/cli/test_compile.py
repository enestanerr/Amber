"""`amber compile` and `amber programs` with the recorded model backend."""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amber.cli.app import app
from amber.project import init_project

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    init_project(tmp_path)
    dest = tmp_path / ".amber" / "traces" / "weekly-report"
    dest.mkdir(parents=True)
    for name in ("t1.json", "t2.json", "t3.json"):
        shutil.copy(FIXTURES / "traces" / "weekly-report" / name, dest / name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMBER_MODEL_BACKEND", "recorded")
    monkeypatch.setenv("AMBER_MODEL_FILLS", str(FIXTURES / "model_fills"))
    return tmp_path


def test_compile_json_and_artifacts(project: Path) -> None:
    r = runner.invoke(app, ["compile", "weekly-report", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["version"] == 1 and data["holes_filled"] >= 2 and data["usable"] is True
    assert data["verify"]["passed"] is True
    assert [h["id"] for h in data["holes"]] == ["transform_step3", "judgment_step4"]
    v1 = project / "programs" / "weekly-report" / "v1"
    program = (v1 / "program.py").read_text(encoding="utf-8")
    ast.parse(program)
    assert "transform_step3" in program and "PROMPT_step4" in program
    manifest = json.loads((v1 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["program_hash"] and manifest["inputs_schema"]["properties"]["week"]
    assert manifest["transforms"][0]["verified_on"] == ["t1", "t2", "t3"]
    assert (v1 / "tests" / "test_replay.py").exists()
    report = (v1 / "REPORT.md").read_text(encoding="utf-8")
    assert "accepted" in report and "Review `program.py`" in report


def test_compile_human_output_and_version_bump(project: Path) -> None:
    first = runner.invoke(app, ["compile", "weekly-report"])
    assert first.exit_code == 0, first.output
    assert "holes filled 2/2" in first.stdout and "v1" in first.stdout
    second = runner.invoke(app, ["compile", "weekly-report", "--json"])
    assert json.loads(second.stdout)["version"] == 1  # same skeleton, v1 usable → reuse
    third = runner.invoke(app, ["compile", "weekly-report", "--refresh", "--json"])
    assert json.loads(third.stdout)["version"] == 2  # --refresh bumps
    ls = runner.invoke(app, ["programs", "ls", "--json"])
    rows = json.loads(ls.stdout)
    assert [row["version"] for row in rows] == [1, 2] and rows[0]["llm_steps"] == 1
    human = runner.invoke(app, ["programs", "ls"])
    assert human.exit_code == 0 and "Programs (2)" in human.stdout and "v1" in human.stdout
    show = runner.invoke(app, ["programs", "show", "weekly-report", "--json"])
    assert (
        json.loads(show.stdout)["version"] == 2 and "@program" in json.loads(show.stdout)["program"]
    )
    diff = runner.invoke(app, ["programs", "diff", "weekly-report", "1", "2", "--json"])
    assert diff.exit_code == 0 and "version=2" in json.loads(diff.stdout)["diff"]
    assert (
        runner.invoke(app, ["programs", "show", "weekly-report", "--version", "9"]).exit_code != 0
    )


def test_compile_dry_run(project: Path) -> None:
    r = runner.invoke(app, ["compile", "weekly-report", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["holes_filled"] == 0 and data["dry_run"] is True
    program = (project / "programs" / "weekly-report" / "v1" / "program.py").read_text(
        encoding="utf-8"
    )
    assert "NotImplementedError" in program


def test_compile_without_traces_fails(project: Path) -> None:
    assert runner.invoke(app, ["compile", "nothing"]).exit_code != 0
