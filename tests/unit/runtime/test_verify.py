"""Verify: compile the fixtures with the recorded backend, replay 3/3, diverge on t_broken,
output comparison, live-llm agreement with the recorded backend, replay_from_paths."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from amber.compile.compiler import CompileResult, compile_workflow
from amber.compile.versions import get_version
from amber.config import AmberConfig
from amber.models.recorded import RecordedBackend
from amber.project import init_project
from amber.runtime.verify import mark_usable, replay_from_paths, verify_version
from amber.trace.models import Trace
from amber.trace.store import TraceStore
from amber.util.json import read_json

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def compiled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, CompileResult, TraceStore]:
    init_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    store = TraceStore(tmp_path / ".amber")
    traces: list[Trace] = []
    for name in ("t1", "t2", "t3", "t_broken"):
        t = Trace.model_validate(read_json(FIXTURES / "traces" / "weekly-report" / f"{name}.json"))
        store.save(t)
        if name != "t_broken":
            traces.append(t)
    backend = RecordedBackend(directory=FIXTURES / "model_fills")
    result = asyncio.run(compile_workflow(tmp_path, AmberConfig(), traces, backend=backend))
    return tmp_path, result, store


def test_compile_verifies_and_marks_usable(
    compiled: tuple[Path, CompileResult, TraceStore],
) -> None:
    root, result, _ = compiled
    assert result.usable is True and result.verify is not None and result.verify.passed
    assert [r.status for r in result.verify.reports] == ["pass", "pass", "pass"]
    assert [r.consumed for r in result.verify.reports] == [5, 5, 5]
    assert all(r.llm_steps == 1 and r.output_match is True for r in result.verify.reports)
    manifest = get_version(root, "weekly-report").manifest()
    assert manifest is not None and manifest.usable is True
    assert manifest.trace_calls["t2"] == [1, 2, 3, 4, 7]  # builtin.Read (event 5) is skipped
    report = (result.path / "REPORT.md").read_text(encoding="utf-8")
    assert "- usable: **yes**" in report and "| t1 | 5/5 | 1 | PASS |" in report


def test_broken_trace_diverges(compiled: tuple[Path, CompileResult, TraceStore]) -> None:
    root, _, store = compiled
    info = get_version(root, "weekly-report")
    report = verify_version(info, [store.load("t_broken")])
    assert not report.passed
    r = report.reports[0]
    assert r.status == "fail" and r.divergence is not None
    assert r.divergence.step_idx == 1 and r.divergence.expected is not None
    assert r.divergence.expected[0] == "shop.top_products" and r.divergence.actual is not None
    assert r.divergence.actual[0] == "shop.list_orders"
    assert "order differs" in r.summary()
    assert "t_broken" in report.rows()[0]["trace"] and report.to_json()["passed"] is False


def test_replay_from_paths_and_generated_test_module(
    compiled: tuple[Path, CompileResult, TraceStore],
) -> None:
    _, result, _ = compiled
    test_file = result.path / "tests" / "test_replay.py"
    report = replay_from_paths(str(test_file), "t1")
    assert report.status == "pass" and report.consumed == 5
    assert 'TRACES = ["t1", "t2", "t3"]' in test_file.read_text(encoding="utf-8")


def test_mark_usable_requires_the_full_source_set(
    compiled: tuple[Path, CompileResult, TraceStore],
) -> None:
    root, _, store = compiled
    info = get_version(root, "weekly-report")
    partial = verify_version(info, [store.load("t1")])
    assert partial.passed
    assert mark_usable(info, partial) is False  # only 1 of 3 source traces verified
    full = verify_version(info, [store.load(t) for t in ("t1", "t2", "t3")])
    assert mark_usable(info, full) is True


def test_live_llm_agreement_with_recorded_backend(
    compiled: tuple[Path, CompileResult, TraceStore],
) -> None:
    root, _, store = compiled
    info = get_version(root, "weekly-report")
    t1 = store.load("t1")
    summary = t1.calls[-1].args["text"]
    fills = {
        "step4": {"text": summary, "confidence": 0.8},
        "judge:step4": {"same_meaning": True, "notes": "identical"},
    }
    backend = RecordedBackend({k: [v] for k, v in fills.items()})
    report = verify_version(info, [t1], live_llm=(backend, "claude-haiku-5-5"))
    assert report.passed
    agreements = report.reports[0].agreements
    assert len(agreements) == 1 and agreements[0].agree and agreements[0].method == "judge"


def test_program_crash_is_reported_not_raised(
    compiled: tuple[Path, CompileResult, TraceStore], tmp_path: Path
) -> None:
    root, result, store = compiled
    broken_dir = tmp_path / "programs" / "weekly-report" / "v9"
    shutil.copytree(result.path, broken_dir)
    src = (broken_dir / "program.py").read_text(encoding="utf-8")
    (broken_dir / "program.py").write_text(
        src.replace('r0["start"]', 'r0["nope"]'), encoding="utf-8"
    )
    info = get_version(root, "weekly-report", 9)
    report = verify_version(info, [store.load("t1")])
    assert (
        not report.passed
        and report.reports[0].error is not None
        and "KeyError" in report.reports[0].error
    )
