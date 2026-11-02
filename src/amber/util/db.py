"""SQLite access with WAL mode and single-writer discipline (``docs/01-architecture.md``).

``Db.write(fn)`` takes the process-level lock file (``<db>.lock``, ``fcntl``/``msvcrt``), opens
a connection with ``isolation_level=None`` and runs *fn* inside ``BEGIN IMMEDIATE … COMMIT``.
Readers open their own connections. Never hold the lock across a network call.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


@contextmanager
def file_lock(path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    """Exclusive advisory lock on *path* (created if missing); polls until *timeout*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"could not acquire lock {path} within {timeout}s") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class Db:
    """One SQLite file, WAL journal, serialized writers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_name(path.stem + ".lock")

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run *fn* in one write transaction under the process lock."""
        with file_lock(self.lock_path):
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    result = fn(conn)
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
                return result
            finally:
                conn.close()

    def ensure_schema(self, ddl: str) -> None:
        """Apply idempotent DDL (``CREATE TABLE IF NOT EXISTS …``) under the lock."""
        with file_lock(self.lock_path):
            conn = self.connect()
            try:
                conn.executescript(ddl)
            finally:
                conn.close()
