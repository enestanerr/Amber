"""`amber record --from-stdin`, `amber import <jsonl>` and `amber import --proxy`."""

from __future__ import annotations

import json
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
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_record_from_stdin_json(project: Path) -> None:
    stream = (FIXTURES / "claude_stream" / "weekly_report_run1.jsonl").read_text(encoding="utf-8")
    r = runner.invoke(
        app,
        ["record", "weekly-report", "--from-stdin", "--input", "week=2026-W42", "--json"],
        input=stream,
    )
    assert r.exit_code == 0, r.output
    t = json.loads(r.stdout)
    assert (
        t["adapter"] == "claude-stream"
        and len(t["events"]) > 0
        and t["inputs"] == {"week": "2026-W42"}
    )
    saved = list((project / ".amber" / "traces" / "weekly-report").glob("*.json"))
    assert len(saved) == 1 and saved[0].stem == t["id"]
    ls = runner.invoke(app, ["traces", "ls", "--json"])
    assert [row["id"] for row in json.loads(ls.stdout)] == [t["id"]]


def test_record_from_stdin_human_summary_and_failure_exit(project: Path) -> None:
    lines = (
        (FIXTURES / "claude_stream" / "weekly_report_run2.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    ok = runner.invoke(
        app,
        ["record", "weekly-report", "--from-stdin", "--input", "week=2026-W43"],
        input="\n".join(lines),
    )
    assert ok.exit_code == 0, ok.output
    assert "Recorded weekly-report" in ok.stdout and "tool calls      6" in ok.stdout
    assert "judgment cands  1" in ok.stdout and "slack.post_message.text" in ok.stdout
    failed = runner.invoke(
        app, ["record", "weekly-report", "--from-stdin"], input="\n".join(lines[:-1])
    )
    assert failed.exit_code == 1


def test_record_bad_input_pair(project: Path) -> None:
    r = runner.invoke(app, ["record", "wf", "--from-stdin", "--input", "novalue"], input="")
    assert r.exit_code != 0


def test_import_transcript_json(project: Path) -> None:
    r = runner.invoke(
        app,
        [
            "import",
            "weekly-report",
            str(FIXTURES / "claude_transcript" / "session.jsonl"),
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    t = json.loads(r.stdout)
    assert t["adapter"] == "claude-transcript" and t["origin"] == "import"
    assert len([e for e in t["events"] if e["kind"] == "tool_call"]) == 4


def test_import_proxy_dir(project: Path) -> None:
    r = runner.invoke(
        app,
        [
            "import",
            "weekly-report",
            "--proxy",
            str(FIXTURES / "mcp_proxy"),
            "--prompt",
            "task",
            "--status",
            "success",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    t = json.loads(r.stdout)
    assert (
        t["adapter"] == "mcp-proxy"
        and t["outcome"]["status"] == "success"
        and t["task_prompt"] == "task"
    )


def test_import_requires_exactly_one_source(project: Path) -> None:
    r = runner.invoke(app, ["import", "wf"])
    assert r.exit_code != 0
    both = runner.invoke(app, ["import", "wf", "x.jsonl", "--latest"])
    assert both.exit_code != 0
