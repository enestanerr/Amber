from __future__ import annotations

from pathlib import Path

import pytest

from amber.align.models import Skeleton
from amber.align.skeleton import build_skeleton
from amber.trace.models import Trace
from amber.util.json import read_json

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def weekly_traces() -> list[Trace]:
    folder = FIXTURES / "traces" / "weekly-report"
    return [Trace.model_validate(read_json(folder / f"t{i}.json")) for i in (1, 2, 3)]


@pytest.fixture
def weekly_skeleton(weekly_traces: list[Trace]) -> Skeleton:
    return build_skeleton(weekly_traces)


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
