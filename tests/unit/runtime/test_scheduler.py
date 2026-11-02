"""Scheduler: cron validation, due selection, loop with an injected clock, CLI commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amber.cli.app import app
from amber.errors import UsageError
from amber.project import init_project
from amber.runtime.ledger import Ledger
from amber.runtime.scheduler import Schedule, Schedules, run_scheduler, validate_cron
from amber.util.time import utcnow

runner = CliRunner()


def test_cron_validation() -> None:
    validate_cron("0 9 * * 1")
    with pytest.raises(UsageError):
        validate_cron("every monday")


def test_schedules_add_due_mark_remove(tmp_path: Path) -> None:
    schedules = Schedules(Ledger(tmp_path / "amber.db"))
    s = schedules.add("weekly-report", "0 9 * * 1", {"week": "2026-W45"})
    assert s.id >= 1 and s.enabled and s.next_run is not None and s.last_run is None
    listed = schedules.list()
    assert [x.workflow for x in listed] == ["weekly-report"] and listed[0].inputs == {
        "week": "2026-W45"
    }
    far_future = datetime(2030, 1, 1, tzinfo=UTC)
    assert [x.id for x in schedules.due(far_future)] == [s.id]
    assert schedules.due(datetime(2000, 1, 1, tzinfo=UTC)) == []
    schedules.mark_ran(s, far_future)
    again = schedules.list()[0]
    assert (
        again.last_run is not None
        and again.next_run is not None
        and again.next_run > again.last_run
    )
    assert schedules.remove(s.id) is True and schedules.remove(s.id) is False


def test_run_scheduler_loop_runs_due_schedules(tmp_path: Path) -> None:
    schedules = Schedules(Ledger(tmp_path / "amber.db"))
    schedules.add("wf", "* * * * *", {})
    ran: list[Schedule] = []
    clock = [utcnow() + timedelta(minutes=2)]  # next_run is computed from the real clock
    count = run_scheduler(
        schedules,
        lambda s: (ran.append(s), "ok")[1],
        log_path=tmp_path / "logs" / "scheduler.log",
        poll_seconds=0,
        max_iterations=2,
        clock=lambda: clock[0],
    )
    assert count == 1 and len(ran) == 1  # second iteration: next_run moved into the future
    assert "schedule 1 wf → ok" in (tmp_path / "logs" / "scheduler.log").read_text(encoding="utf-8")

    def boom(_: Schedule) -> str:
        raise RuntimeError("nope")

    clock[0] += timedelta(hours=1)
    assert (
        run_scheduler(schedules, boom, poll_seconds=0, max_iterations=1, clock=lambda: clock[0])
        == 1
    )


def test_schedule_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    added = runner.invoke(
        app,
        [
            "schedule",
            "add",
            "weekly-report",
            "--cron",
            "0 9 * * 1",
            "--input",
            "week=2026-W45",
            "--json",
        ],
    )
    assert added.exit_code == 0, added.output
    sid = json.loads(added.stdout)["id"]
    listed = runner.invoke(app, ["schedule", "ls", "--json"])
    assert [row["id"] for row in json.loads(listed.stdout)] == [sid]
    assert "0 9 * * 1" in runner.invoke(app, ["schedule", "ls"]).stdout
    assert runner.invoke(app, ["schedule", "add", "wf", "--cron", "bad"]).exit_code != 0
    removed = runner.invoke(app, ["schedule", "rm", str(sid), "--json"])
    assert json.loads(removed.stdout) == {"removed": sid}
    assert runner.invoke(app, ["schedule", "rm", "99"]).exit_code != 0
    once = runner.invoke(app, ["scheduler", "--once"])
    assert once.exit_code == 0 and "ran 0 schedule(s)" in once.stdout
