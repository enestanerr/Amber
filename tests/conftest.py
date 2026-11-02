"""Shared pytest configuration.

* ``live`` tests are skipped unless explicitly selected (``-m live``) — unit runs never touch
  the network.
* Environment variables that change Amber's behaviour are cleared for every test.
* ``fake_claude`` puts a ``claude`` shim on PATH that replays a stream-json fixture.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

_AMBER_ENV = (
    "ANTHROPIC_API_KEY",
    "AMBER_HOME",
    "AMBER_PROJECT",
    "AMBER_MODEL_BACKEND",
    "AMBER_LOG",
    "AMBER_SCENARIO",
    "AMBER_OUTBOX",
)

FAKE_CLAUDE = """
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


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite golden files (tests/unit/compile/golden) instead of comparing.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    markexpr = str(config.getoption("-m") or "")
    if "live" in markexpr:
        return
    skip = pytest.mark.skip(reason="live test: run with `pytest -m live`")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AMBER_ENV:
        monkeypatch.delenv(name, raising=False)


def install_fake_claude(bin_dir: Path, monkeypatch: pytest.MonkeyPatch, fixture: Path) -> Path:
    """Create the shim in *bin_dir*, put it on PATH, point it at *fixture*; returns bin_dir."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    if sys.platform == "win32":
        shim = bin_dir / "claude.bat"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = bin_dir / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(fixture))
    monkeypatch.setenv("FAKE_CLAUDE_ARGV", str(bin_dir / "argv.txt"))
    return bin_dir


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `claude` shim on PATH replaying weekly_report_run1.jsonl (override FAKE_CLAUDE_FIXTURE)."""
    fixture = (
        Path(__file__).resolve().parent / "fixtures" / "claude_stream" / "weekly_report_run1.jsonl"
    )
    return install_fake_claude(tmp_path / "fake-bin", monkeypatch, fixture)
