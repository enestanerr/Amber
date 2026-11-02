"""Trace store: JSON files are the source of truth, SQLite is the index.

Layout: ``<home>/traces/<workflow>/<trace_id>.json``; index table ``traces`` in
``<home>/amber.db`` (``docs/01-architecture.md``). :meth:`TraceStore.sync` reconciles the index
with the files, so traces copied in by hand (fixtures, backups) are picked up.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from amber.errors import AmberError, UsageError
from amber.trace.models import Trace
from amber.util.db import Db
from amber.util.json import read_json, write_json

TRACES_DDL = """
CREATE TABLE IF NOT EXISTS traces (
  id TEXT PRIMARY KEY, workflow TEXT, adapter TEXT, created_at TEXT, status TEXT,
  calls INTEGER, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
  origin TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS traces_workflow_created ON traces (workflow, created_at);
"""

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class TraceNotFound(AmberError):
    exit_code = 1


def validate_name(kind: str, value: str) -> str:
    """Workflow names and trace ids become path components: keep them boring."""
    if not _NAME_RE.match(value) or value in (".", ".."):
        raise UsageError(
            f"invalid {kind} {value!r}",
            hint="Use letters, digits, '-', '_' or '.', starting with a letter or digit.",
        )
    return value


class TraceSummary(BaseModel):
    """One row of the ``traces`` index."""

    model_config = ConfigDict(extra="forbid")

    id: str
    workflow: str
    adapter: str
    created_at: datetime
    status: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    origin: str
    path: str

    @classmethod
    def from_trace(cls, trace: Trace, path: Path) -> TraceSummary:
        return cls(
            id=trace.id,
            workflow=trace.workflow,
            adapter=trace.adapter,
            created_at=trace.created_at,
            status=trace.outcome.status,
            calls=len(trace.calls),
            input_tokens=trace.outcome.usage.total_input,
            output_tokens=trace.outcome.usage.output_tokens,
            cost_usd=trace.outcome.cost_usd,
            origin=trace.origin,
            path=str(path),
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TraceSummary:
        return cls(
            id=row["id"],
            workflow=row["workflow"],
            adapter=row["adapter"],
            created_at=datetime.fromisoformat(row["created_at"]),
            status=row["status"],
            calls=row["calls"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            origin=row["origin"],
            path=row["path"],
        )


class TraceStore:
    def __init__(self, home: Path) -> None:
        # absolute, so the index never stores paths that depend on the caller's cwd
        self.home = home.absolute()
        self.traces_dir = self.home / "traces"
        self.db = Db(self.home / "amber.db")
        self.db.ensure_schema(TRACES_DDL)

    # -- paths -------------------------------------------------------------
    def path_for(self, workflow: str, trace_id: str) -> Path:
        validate_name("workflow", workflow)
        validate_name("trace id", trace_id)
        return self.traces_dir / workflow / f"{trace_id}.json"

    # -- write -------------------------------------------------------------
    def save(self, trace: Trace) -> Path:
        """Validate, write the JSON file, upsert the index row. Returns the file path."""
        trace.check()
        path = self.path_for(trace.workflow, trace.id)
        write_json(path, trace.model_dump(mode="json"))
        self._index(TraceSummary.from_trace(trace, path))
        return path

    def delete(self, trace_id: str) -> bool:
        summary = self._lookup(trace_id)
        if summary is None:
            self.sync()
            summary = self._lookup(trace_id)
        if summary is None:
            return False
        Path(summary.path).unlink(missing_ok=True)
        self.db.write(lambda c: c.execute("DELETE FROM traces WHERE id = ?", (trace_id,)))
        return True

    # -- read --------------------------------------------------------------
    def load(self, trace_id: str) -> Trace:
        summary = self._lookup(trace_id)
        if summary is None:
            self.sync()
            summary = self._lookup(trace_id)
        if summary is None:
            raise TraceNotFound(
                f"no trace {trace_id!r}", hint="List traces with `amber traces ls`."
            )
        return Trace.model_validate(read_json(Path(summary.path)))

    def list(self, workflow: str | None = None) -> list[TraceSummary]:
        """Chronological (oldest first) summaries, optionally for one workflow."""
        self.sync()
        with self.db.read() as conn:
            if workflow is None:
                rows = conn.execute("SELECT * FROM traces ORDER BY created_at, id").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM traces WHERE workflow = ? ORDER BY created_at, id", (workflow,)
                ).fetchall()
        return [TraceSummary.from_row(r) for r in rows]

    def latest(self, workflow: str) -> Trace | None:
        rows = self.list(workflow)
        return self.load(rows[-1].id) if rows else None

    def load_all(self, workflow: str) -> list[Trace]:
        return [self.load(s.id) for s in self.list(workflow)]

    # -- index maintenance -------------------------------------------------
    def sync(self) -> int:
        """Index files missing from the index; drop rows whose file is gone. Returns #changes."""
        known: dict[str, str] = {}
        with self.db.read() as conn:
            for row in conn.execute("SELECT id, path FROM traces"):
                known[row["id"]] = row["path"]
        changes = 0
        on_disk: set[str] = set()
        reindexed: set[str] = set()
        if self.traces_dir.is_dir():
            for file in sorted(self.traces_dir.glob("*/*.json")):
                on_disk.add(str(file))
                if str(file) in known.values():
                    continue
                trace = Trace.model_validate(read_json(file))
                self._index(TraceSummary.from_trace(trace, file))
                reindexed.add(trace.id)
                changes += 1
        # a row whose file was just re-indexed under another spelling of its path is not stale
        stale = [tid for tid, path in known.items() if path not in on_disk and tid not in reindexed]
        if stale:
            self.db.write(
                lambda c: c.executemany("DELETE FROM traces WHERE id = ?", [(t,) for t in stale])
            )
            changes += len(stale)
        return changes

    def _index(self, s: TraceSummary) -> None:
        self.db.write(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO traces (id, workflow, adapter, created_at, status, calls,"
                " input_tokens, output_tokens, cost_usd, origin, path)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    s.id,
                    s.workflow,
                    s.adapter,
                    s.created_at.isoformat(),
                    s.status,
                    s.calls,
                    s.input_tokens,
                    s.output_tokens,
                    s.cost_usd,
                    s.origin,
                    s.path,
                ),
            )
        )

    def _lookup(self, trace_id: str) -> TraceSummary | None:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM traces WHERE id = ?", (trace_id,)).fetchone()
        return TraceSummary.from_row(row) if row is not None else None
