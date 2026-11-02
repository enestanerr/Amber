"""TraceStore: roundtrip, listing order, latest, delete, sync, single-writer lock."""

from __future__ import annotations

import shutil
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

from amber.errors import UsageError
from amber.trace.models import Trace
from amber.trace.store import TraceNotFound, TraceStore
from amber.util.db import Db


def test_save_load_list_latest_delete(tmp_path: Path, weekly_traces: list[Trace]) -> None:
    store = TraceStore(tmp_path / ".amber")
    t1, t2, t3 = weekly_traces
    for t in (t3, t1, t2):  # out of order on purpose
        path = store.save(t)
        assert path == tmp_path / ".amber" / "traces" / "weekly-report" / f"{t.id}.json"
        assert path.is_file()
    assert store.load("t2") == t2
    listed = store.list("weekly-report")
    assert [s.id for s in listed] == ["t1", "t2", "t3"]  # chronological
    assert listed[0].calls == 5 and listed[1].calls == 6
    assert listed[0].input_tokens == t1.outcome.usage.total_input
    assert store.list("other") == []
    latest = store.latest("weekly-report")
    assert latest is not None and latest.id == "t3"
    assert store.latest("other") is None
    assert store.delete("t2") is True
    assert store.delete("t2") is False
    assert [s.id for s in store.list()] == ["t1", "t3"]
    with pytest.raises(TraceNotFound):
        store.load("t2")


def test_sync_indexes_files_copied_in_and_drops_stale_rows(
    tmp_path: Path, fixture_dir: Path, weekly_traces: list[Trace]
) -> None:
    home = tmp_path / ".amber"
    dest = home / "traces" / "weekly-report"
    dest.mkdir(parents=True)
    for name in ("t1.json", "t3.json"):
        shutil.copy(fixture_dir / name, dest / name)
    store = TraceStore(home)
    assert [s.id for s in store.list()] == ["t1", "t3"]
    (dest / "t3.json").unlink()
    assert store.sync() == 1
    assert [s.id for s in store.list()] == ["t1"]
    assert store.load("t1") == weekly_traces[0]


def test_invalid_names_rejected(tmp_path: Path, t1: Trace) -> None:
    store = TraceStore(tmp_path / ".amber")
    with pytest.raises(UsageError):
        store.path_for("../etc", "t1")
    with pytest.raises(UsageError):
        store.path_for("wf", "..")


def test_save_rejects_invariant_violations(tmp_path: Path, t1: Trace) -> None:
    store = TraceStore(tmp_path / ".amber")
    t1.events[0].idx = 9
    with pytest.raises(ValueError):
        store.save(t1)


def test_created_at_ordering_uses_timestamp_not_id(tmp_path: Path, t1: Trace) -> None:
    store = TraceStore(tmp_path / ".amber")
    later = t1.model_copy(
        update={"id": "a_earlier_id", "created_at": t1.created_at + timedelta(days=1)}
    )
    store.save(later)
    store.save(t1)
    assert [s.id for s in store.list()] == ["t1", "a_earlier_id"]


def test_db_write_is_serialized_across_threads(tmp_path: Path) -> None:
    db = Db(tmp_path / "x.db")
    db.ensure_schema("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, v INTEGER);")
    db.write(lambda c: c.execute("INSERT INTO t VALUES ('n', 0)"))
    overlaps: list[bool] = []
    active = threading.Event()

    def bump(_: int) -> None:
        def tx(conn: object) -> None:
            overlaps.append(active.is_set())
            active.set()
            time.sleep(0.01)
            c = conn  # type: ignore[assignment]
            (v,) = c.execute("SELECT v FROM t WHERE k='n'").fetchone()  # type: ignore[attr-defined]
            c.execute("UPDATE t SET v=? WHERE k='n'", (v + 1,))  # type: ignore[attr-defined]
            active.clear()

        db.write(tx)

    threads = [threading.Thread(target=bump, args=(i,)) for i in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    with db.read() as conn:
        (v,) = conn.execute("SELECT v FROM t WHERE k='n'").fetchone()
    assert v == 6
    assert not any(overlaps), "a second writer entered while one was active"


def test_db_rollback_on_error(tmp_path: Path) -> None:
    db = Db(tmp_path / "x.db")
    db.ensure_schema("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY);")

    def failing(conn: object) -> None:
        conn.execute("INSERT INTO t VALUES ('a')")  # type: ignore[attr-defined]
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        db.write(failing)
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
