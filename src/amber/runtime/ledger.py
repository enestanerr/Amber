"""Ledger: SQLite tables ``runs``, ``run_steps``, ``guard_events``, ``llm_calls``,
``decision_records`` and ``schedules`` (``docs/01-architecture.md``, frozen at P6).

Single-writer discipline through :class:`~amber.util.db.Db`; readers open their own
connections.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from amber.util.db import Db
from amber.util.json import dumps_compact, loads
from amber.util.time import iso, new_id, utcnow

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, workflow TEXT, version INTEGER,
  started_at TEXT, finished_at TEXT, status TEXT, mode TEXT, inputs TEXT, steps INTEGER,
  llm_calls INTEGER, llm_input_tokens INTEGER, llm_output_tokens INTEGER, cost_usd REAL,
  agent_turns INTEGER, stop_kind TEXT, stop_detail TEXT);
CREATE TABLE IF NOT EXISTS run_steps (run_id TEXT, idx INTEGER, kind TEXT, tool TEXT, args TEXT,
  result_digest TEXT, duration_ms INTEGER, status TEXT, PRIMARY KEY (run_id, idx));
CREATE TABLE IF NOT EXISTS guard_events (id INTEGER PRIMARY KEY, run_id TEXT, idx INTEGER,
  guard TEXT, severity TEXT, detail TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS llm_calls (id INTEGER PRIMARY KEY, run_id TEXT, idx INTEGER,
  step_id TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, valid INTEGER,
  confidence REAL, ts TEXT);
CREATE TABLE IF NOT EXISTS decision_records (id INTEGER PRIMARY KEY, run_id TEXT, workflow TEXT,
  ts TEXT, proposal TEXT, vetoes TEXT, decision TEXT, reason TEXT, actor TEXT);
CREATE TABLE IF NOT EXISTS schedules (id INTEGER PRIMARY KEY, workflow TEXT, cron TEXT,
  inputs TEXT, enabled INTEGER, last_run TEXT, next_run TEXT);
CREATE INDEX IF NOT EXISTS runs_workflow_started ON runs (workflow, started_at);
"""


@dataclass
class RunRow:
    id: str
    workflow: str
    version: int
    started_at: str
    finished_at: str | None
    status: str
    mode: str
    inputs: dict[str, Any]
    steps: int
    llm_calls: int
    llm_input_tokens: int
    llm_output_tokens: int
    cost_usd: float | None
    agent_turns: int
    stop_kind: str | None
    stop_detail: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RunRow:
        return cls(
            id=row["id"],
            workflow=row["workflow"],
            version=row["version"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            mode=row["mode"],
            inputs=loads(row["inputs"] or "{}"),
            steps=row["steps"] or 0,
            llm_calls=row["llm_calls"] or 0,
            llm_input_tokens=row["llm_input_tokens"] or 0,
            llm_output_tokens=row["llm_output_tokens"] or 0,
            cost_usd=row["cost_usd"],
            agent_turns=row["agent_turns"] or 0,
            stop_kind=row["stop_kind"],
            stop_detail=row["stop_detail"],
        )


class Ledger:
    def __init__(self, db_path: Path) -> None:
        self.db = Db(db_path)
        self.db.ensure_schema(LEDGER_DDL)

    # -- runs ----------------------------------------------------------------
    def open_run(self, workflow: str, version: int, mode: str, inputs: dict[str, Any]) -> str:
        run_id = new_id("r")
        self.db.write(
            lambda c: c.execute(
                "INSERT INTO runs (id, workflow, version, started_at, status, mode, inputs, steps,"
                " llm_calls, llm_input_tokens, llm_output_tokens, agent_turns)"
                " VALUES (?, ?, ?, ?, 'running', ?, ?, 0, 0, 0, 0, 0)",
                (run_id, workflow, version, iso(utcnow()), mode, dumps_compact(inputs)),
            )
        )
        return run_id

    def close_run(
        self,
        run_id: str,
        *,
        status: str,
        steps: int,
        llm_calls: int,
        llm_input_tokens: int,
        llm_output_tokens: int,
        cost_usd: float | None,
        agent_turns: int = 0,
        stop_kind: str | None = None,
        stop_detail: str | None = None,
    ) -> None:
        self.db.write(
            lambda c: c.execute(
                "UPDATE runs SET finished_at=?, status=?, steps=?, llm_calls=?, llm_input_tokens=?,"
                " llm_output_tokens=?, cost_usd=?, agent_turns=?, stop_kind=?, stop_detail=?"
                " WHERE id=?",
                (
                    iso(utcnow()),
                    status,
                    steps,
                    llm_calls,
                    llm_input_tokens,
                    llm_output_tokens,
                    cost_usd,
                    agent_turns,
                    stop_kind,
                    stop_detail,
                    run_id,
                ),
            )
        )

    def add_step(
        self,
        run_id: str,
        idx: int,
        *,
        kind: str,
        tool: str,
        args: dict[str, Any],
        result_digest: str,
        duration_ms: int,
        status: str,
    ) -> None:
        self.db.write(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO run_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, idx, kind, tool, dumps_compact(args), result_digest, duration_ms, status),
            )
        )

    def add_guard_event(
        self, run_id: str, idx: int, guard: str, severity: str, detail: str
    ) -> None:
        self.db.write(
            lambda c: c.execute(
                "INSERT INTO guard_events (run_id, idx, guard, severity, detail, ts)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, idx, guard, severity, detail, iso(utcnow())),
            )
        )

    def add_llm_call(
        self,
        run_id: str,
        idx: int,
        step_id: str,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        valid: bool,
        confidence: float | None,
    ) -> None:
        self.db.write(
            lambda c: c.execute(
                "INSERT INTO llm_calls (run_id, idx, step_id, model, input_tokens, output_tokens,"
                " valid,"
                " confidence, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    idx,
                    step_id,
                    model,
                    input_tokens,
                    output_tokens,
                    int(valid),
                    confidence,
                    iso(utcnow()),
                ),
            )
        )

    def add_decision(
        self,
        run_id: str,
        workflow: str,
        *,
        proposal: Any,
        vetoes: Any,
        decision: str,
        reason: str,
        actor: str,
    ) -> None:
        self.db.write(
            lambda c: c.execute(
                "INSERT INTO decision_records (run_id, workflow, ts, proposal, vetoes, decision,"
                " reason, actor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    workflow,
                    iso(utcnow()),
                    dumps_compact(proposal),
                    dumps_compact(vetoes),
                    decision,
                    reason,
                    actor,
                ),
            )
        )

    # -- reads ---------------------------------------------------------------
    def run(self, run_id: str) -> RunRow | None:
        with self.db.read() as c:
            row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return RunRow.from_row(row) if row else None

    def recent_runs(self, workflow: str | None = None, n: int = 10) -> list[RunRow]:
        with self.db.read() as c:
            if workflow is None:
                rows = c.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (n,)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM runs WHERE workflow = ?"
                    " ORDER BY started_at DESC, rowid DESC LIMIT ?",
                    (workflow, n),
                ).fetchall()
        return [RunRow.from_row(r) for r in rows]

    def steps(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT * FROM run_steps WHERE run_id = ? ORDER BY idx", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def guard_events(
        self, run_id: str | None = None, *, workflow: str | None = None, n: int = 200
    ) -> list[dict[str, Any]]:
        with self.db.read() as c:
            if run_id is not None:
                rows = c.execute(
                    "SELECT * FROM guard_events WHERE run_id = ? ORDER BY id", (run_id,)
                ).fetchall()
            elif workflow is not None:
                rows = c.execute(
                    "SELECT g.* FROM guard_events g JOIN runs r ON r.id = g.run_id"
                    " WHERE r.workflow = ?"
                    " ORDER BY g.id DESC LIMIT ?",
                    (workflow, n),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM guard_events ORDER BY id DESC LIMIT ?", (n,)
                ).fetchall()
        return [dict(r) for r in rows]

    def llm_calls(
        self, run_id: str | None = None, *, workflow: str | None = None, n: int = 200
    ) -> list[dict[str, Any]]:
        with self.db.read() as c:
            if run_id is not None:
                rows = c.execute(
                    "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,)
                ).fetchall()
            elif workflow is not None:
                rows = c.execute(
                    "SELECT l.* FROM llm_calls l JOIN runs r ON r.id = l.run_id"
                    " WHERE r.workflow = ?"
                    " ORDER BY l.id DESC LIMIT ?",
                    (workflow, n),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (n,)
                ).fetchall()
        return [dict(r) for r in rows]

    def decisions(self, workflow: str | None = None, n: int = 200) -> list[dict[str, Any]]:
        with self.db.read() as c:
            if workflow is None:
                rows = c.execute(
                    "SELECT * FROM decision_records ORDER BY id DESC LIMIT ?", (n,)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM decision_records WHERE workflow = ? ORDER BY id DESC LIMIT ?",
                    (workflow, n),
                ).fetchall()
        return [dict(r) for r in rows]
