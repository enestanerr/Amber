"""Minimal in-process scheduler (``docs/05-runtime.md`` §7, decision D11).

Schedules live in the ledger's ``schedules`` table. ``run_scheduler`` polls every 30 s and runs
every enabled schedule whose ``next_run`` is due, one program at a time. Prefer system cron or
GitHub Actions calling ``amber run`` for anything serious; this exists so the loop can be
closed without extra infrastructure.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from croniter import croniter

from amber.errors import UsageError
from amber.runtime.ledger import Ledger
from amber.util.json import dumps_compact, loads
from amber.util.logging import get_logger
from amber.util.time import iso, utcnow

log = get_logger("runtime.scheduler")
POLL_SECONDS = 30.0


@dataclass
class Schedule:
    id: int
    workflow: str
    cron: str
    inputs: dict[str, Any]
    enabled: bool
    last_run: str | None
    next_run: str | None


def validate_cron(expr: str) -> None:
    if not croniter.is_valid(expr):
        raise UsageError(f"invalid cron expression {expr!r}", hint='Example: --cron "0 9 * * 1"')


def next_after(expr: str, after: datetime) -> datetime:
    return croniter(expr, after).get_next(datetime)


class Schedules:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def add(self, workflow: str, cron: str, inputs: dict[str, Any]) -> Schedule:
        validate_cron(cron)
        nxt = iso(next_after(cron, utcnow()))

        def insert(conn: Any) -> int:
            cur = conn.execute(
                "INSERT INTO schedules (workflow, cron, inputs, enabled, last_run, next_run)"
                " VALUES (?, ?, ?, 1, NULL, ?)",
                (workflow, cron, dumps_compact(inputs), nxt),
            )
            return int(cur.lastrowid)

        sid = self.ledger.db.write(insert)
        return Schedule(sid, workflow, cron, dict(inputs), True, None, nxt)

    def list(self) -> list[Schedule]:
        with self.ledger.db.read() as conn:
            rows = conn.execute("SELECT * FROM schedules ORDER BY id").fetchall()
        return [
            Schedule(
                r["id"],
                r["workflow"],
                r["cron"],
                loads(r["inputs"] or "{}"),
                bool(r["enabled"]),
                r["last_run"],
                r["next_run"],
            )
            for r in rows
        ]

    def remove(self, schedule_id: int) -> bool:
        def delete(conn: Any) -> int:
            return int(conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,)).rowcount)

        return self.ledger.db.write(delete) > 0

    def mark_ran(self, schedule: Schedule, now: datetime) -> None:
        nxt = iso(next_after(schedule.cron, now))
        self.ledger.db.write(
            lambda c: c.execute(
                "UPDATE schedules SET last_run = ?, next_run = ? WHERE id = ?",
                (iso(now), nxt, schedule.id),
            )
        )

    def due(self, now: datetime) -> list[Schedule]:
        stamp = iso(now)
        return [
            s for s in self.list() if s.enabled and s.next_run is not None and s.next_run <= stamp
        ]


def run_scheduler(
    schedules: Schedules,
    runner: Callable[[Schedule], str],
    *,
    log_path: Path | None = None,
    poll_seconds: float = POLL_SECONDS,
    max_iterations: int | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> int:
    """Foreground loop; ``runner(schedule)`` executes one run and returns its status."""
    iterations = 0
    ran = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        now = clock()
        for schedule in schedules.due(now):
            try:
                status = runner(schedule)
            except Exception as e:  # keep the loop alive; the ledger has the details
                status = f"failed: {type(e).__name__}: {e}"
            schedules.mark_ran(schedule, now)
            ran += 1
            line = f"{iso(now)} schedule {schedule.id} {schedule.workflow} → {status}"
            log.info("%s", line)
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        if max_iterations is None or iterations < max_iterations:
            time.sleep(poll_seconds)
    return ran
