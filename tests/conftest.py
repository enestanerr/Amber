"""Shared pytest configuration.

* ``live`` tests are skipped unless explicitly selected (``-m live``) — unit runs never touch
  the network.
* Environment variables that change Amber's behaviour are cleared for every test.
"""

from __future__ import annotations

import pytest

_AMBER_ENV = (
    "ANTHROPIC_API_KEY",
    "AMBER_HOME",
    "AMBER_PROJECT",
    "AMBER_MODEL_BACKEND",
    "AMBER_LOG",
)


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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AMBER_ENV:
        monkeypatch.delenv(name, raising=False)
