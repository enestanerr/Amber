"""claude-stream adapter: stdin replay, failure paths and a fake `claude` subprocess."""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest

from amber.adapters.base import RecordRequest
from amber.adapters.claude_stream import record_from_lines, resolve_claude, run_claude
from amber.errors import AmberError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "claude_stream"
RUN1 = FIXTURES / "weekly_report_run1.jsonl"

FAKE = """
import os, sys
if "--version" in sys.argv:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
with open(os.environ["FAKE_CLAUDE_ARGV"], "w", encoding="utf-8") as f:
    f.write(chr(10).join(sys.argv[1:]))
lines = open(os.environ["FAKE_CLAUDE_FIXTURE"], encoding="utf-8").read().splitlines()
drop = int(os.environ.get("FAKE_CLAUDE_DROP", "0"))
sys.stderr.write("fake claude: starting" + chr(10))
for line in lines[: len(lines) - drop]:
    sys.stdout.write(line + chr(10))
    sys.stdout.flush()
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a `claude` shim on PATH that replays a fixture (see FAKE_CLAUDE_* env)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "fake_claude.py"
    script.write_text(FAKE, encoding="utf-8")
    if sys.platform == "win32":
        shim = bin_dir / "claude.bat"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = bin_dir / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(RUN1))
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(tmp_path / "argv.txt"))
    return tmp_path


def _request(**kw: object) -> RecordRequest:
    base: dict[str, object] = {
        "workflow": "weekly-report",
        "agent_command": ["claude", "-p", "task"],
        "inputs": {"week": "2026-W42"},
    }
    base.update(kw)
    return RecordRequest(**base)  # type: ignore[arg-type]


def test_record_from_lines_success() -> None:
    lines = RUN1.read_text(encoding="utf-8").splitlines()
    rec = record_from_lines(_request(), lines, task_prompt="t")
    t = rec.trace
    assert t.adapter == "claude-stream" and t.outcome.status == "success" and len(t.calls) == 5
    assert t.id.startswith("t_") and t.task_prompt == "t" and t.session_id == "s_t1"
    assert t.calls[0].started_at is None and t.calls[0].duration_ms is None  # no clock on replay


def test_record_from_lines_without_result_is_failure() -> None:
    lines = RUN1.read_text(encoding="utf-8").splitlines()[:-1]
    rec = record_from_lines(_request(), lines)
    assert rec.trace.outcome.status == "failure"
    forced = record_from_lines(_request(status_override="success"), lines)
    assert forced.trace.outcome.status == "success"


def test_resolve_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(AmberError, match="not found on PATH"):
        resolve_claude("claude")


def test_run_claude_streams_and_injects_flags(fake_claude: Path) -> None:
    seen: list[str] = []
    # inject_inputs=False keeps the prompt single-line: the .bat shim used on Windows would
    # mangle a newline. The suffix itself is covered by test_flags.
    request = _request(mcp_config=Path("mcp.json"), allow=["mcp__shop__*"], inject_inputs=False)
    rec = asyncio.run(run_claude(request, on_call=lambda c: seen.append(c.tool)))
    assert rec.exit_code == 0 and rec.trace.outcome.status == "success"
    assert seen[:2] == ["shop.week_range", "shop.list_orders"] and len(seen) == 5
    assert rec.trace.harness["version"] == "9.9.9"
    first = rec.trace.calls[0]
    assert first.started_at is not None and first.duration_ms is not None
    argv = (fake_claude / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert argv[:3] == ["-p", "task", "--output-format"]
    assert "stream-json" in argv and "--verbose" in argv
    assert "mcp.json" in argv and "mcp__shop__*" in argv
    assert rec.trace.task_prompt == "task"


def test_run_claude_nonzero_exit_is_failure_with_stderr_tail(
    fake_claude: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_EXIT", "3")
    monkeypatch.setenv("FAKE_CLAUDE_DROP", "1")
    rec = asyncio.run(run_claude(_request(inject_inputs=False)))
    assert rec.exit_code == 3 and rec.trace.outcome.status == "failure"
    assert "fake claude: starting" in rec.stderr_tail
    assert len(rec.trace.calls) == 5  # partial trace is still saved for debugging
    argv = (fake_claude / "argv.txt").read_text(encoding="utf-8").splitlines()
    assert argv[1] == "task"
