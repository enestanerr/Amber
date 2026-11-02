from __future__ import annotations

from pathlib import Path

import pytest

from amber.trace.models import Trace
from amber.util.json import read_json

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "traces" / "weekly-report"


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURES


@pytest.fixture
def t1() -> Trace:
    return Trace.model_validate(read_json(FIXTURES / "t1.json"))


@pytest.fixture
def weekly_traces() -> list[Trace]:
    return [Trace.model_validate(read_json(FIXTURES / f"t{i}.json")) for i in (1, 2, 3)]
