"""`amber verify`: 3/3 pass (exit 0), t_broken diverges (exit 3), --json, --unordered."""

from __future__ import annotations

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
    compiled = runner.invoke(app, ["compile", "weekly-report", "--json"])
    assert compiled.exit_code == 0, compiled.output
    assert json.loads(compiled.stdout)["usable"] is True
    # the broken trace is only for verify; it must not take part in the compile
    shutil.copy(FIXTURES / "traces" / "weekly-report" / "t_broken.json", dest / "t_broken.json")
    return tmp_path


def test_verify_passes_and_json(project: Path) -> None:
    r = runner.invoke(app, ["verify", "weekly-report"])
    assert r.exit_code == 0, r.output
    assert "3/3 PASS" in r.stdout
    j = runner.invoke(app, ["verify", "weekly-report", "--json"])
    data = json.loads(j.stdout)
    assert data["passed"] is True and [t["status"] for t in data["traces"]] == ["pass"] * 3
    assert data["traces"][0]["consumed"] == 5


def test_verify_broken_trace_exits_3(project: Path) -> None:
    r = runner.invoke(app, ["verify", "weekly-report", "--trace", "t_broken"])
    assert r.exit_code == 3, r.output
    assert "FAIL" in r.stdout and "diverged" in r.stdout
    manifest = json.loads(
        (project / "programs" / "weekly-report" / "v1" / "manifest.json").read_text()
    )
    assert manifest["usable"] is True  # a partial verify never changes usable


def test_verify_unordered_and_version_flags(project: Path) -> None:
    assert runner.invoke(app, ["verify", "weekly-report", "--unordered"]).exit_code == 0
    assert (
        runner.invoke(app, ["verify", "weekly-report", "--version", "1", "--trace", "t2"]).exit_code
        == 0
    )
    assert runner.invoke(app, ["verify", "weekly-report", "--version", "5"]).exit_code != 0


def test_compile_reuses_version_when_usable(project: Path) -> None:
    (project / ".amber" / "traces" / "weekly-report" / "t_broken.json").unlink()
    again = runner.invoke(app, ["compile", "weekly-report", "--json"])
    assert json.loads(again.stdout)["version"] == 1  # same skeleton, v1 usable → overwrite in place
    refreshed = runner.invoke(app, ["compile", "weekly-report", "--refresh", "--json"])
    assert json.loads(refreshed.stdout)["version"] == 2
