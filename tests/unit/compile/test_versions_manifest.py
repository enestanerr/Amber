from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from amber.align.models import Skeleton
from amber.compile.manifest import Budgets, Manifest, build_manifest, infer_result_schema
from amber.compile.versions import (
    ProgramNotFound,
    get_version,
    list_versions,
    list_workflows,
    next_version,
    write_version,
)
from amber.config import BudgetsCfg
from amber.trace.models import Trace


def _manifest(version: int, *, usable: bool, skeleton_hash: str = "abc") -> Manifest:
    return Manifest(
        workflow="wf",
        version=version,
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        source_traces=["t1"],
        skeleton_hash=skeleton_hash,
        budgets=Budgets(max_calls=4, max_llm_tokens=100, wall_clock_s=10),
        usable=usable,
    )


def test_version_rules(tmp_path: Path) -> None:
    assert next_version(tmp_path, "wf", "abc", refresh=False) == 1
    assert list_versions(tmp_path, "wf") == [] and list_workflows(tmp_path) == []
    info = write_version(
        tmp_path,
        _manifest(1, usable=False),
        program_source="# p\n",
        test_source="# t\n",
        report="# r\n",
    )
    assert info.path == tmp_path / "programs" / "wf" / "v1"
    assert (info.path / "tests" / "test_replay.py").read_text() == "# t\n" and (
        info.path / "REPORT.md"
    ).exists()
    assert next_version(tmp_path, "wf", "abc", refresh=False) == 2  # v1 not usable → bump
    write_version(
        tmp_path, _manifest(2, usable=True), program_source="# p2\n", test_source="", report=""
    )
    assert next_version(tmp_path, "wf", "abc", refresh=False) == 2  # same skeleton, usable → reuse
    assert next_version(tmp_path, "wf", "zzz", refresh=False) == 3  # skeleton changed
    assert next_version(tmp_path, "wf", "abc", refresh=True) == 3  # --refresh
    assert [v.version for v in list_versions(tmp_path, "wf")] == [1, 2]
    assert list_workflows(tmp_path) == ["wf"]
    assert get_version(tmp_path, "wf").version == 2 and get_version(tmp_path, "wf", 1).version == 1
    assert get_version(tmp_path, "wf", usable_only=True).version == 2
    with pytest.raises(ProgramNotFound):
        get_version(tmp_path, "wf", 9)
    with pytest.raises(ProgramNotFound):
        get_version(tmp_path, "other")


def test_infer_result_schema_required_keys() -> None:
    schema = infer_result_schema([{"a": 1, "b": "x"}, {"a": 2}])
    assert schema["type"] == "object" and schema["required"] == ["a"]
    assert set(schema["properties"]) == {"a", "b"}
    arr = infer_result_schema([[{"id": 1}], [{"id": 2, "x": 1}]])
    assert arr["type"] == "array" and arr["items"]["required"] == ["id"]


def test_build_manifest(weekly_skeleton: Skeleton, weekly_traces: list[Trace]) -> None:
    m = build_manifest(
        weekly_skeleton,
        weekly_traces,
        version=1,
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        budgets=BudgetsCfg(),
        inputs_schema={},
        output_schema={},
        steps=[],
        llm_steps=[],
        transforms=[],
        warnings=["w"],
    )
    assert [t.canonical for t in m.tools] == [
        "shop.week_range",
        "shop.list_orders",
        "shop.top_products",
        "shop.rfm_snapshot",
        "slack.post_message",
    ]
    assert m.servers == {
        "shop": ["week_range", "list_orders", "top_products", "rfm_snapshot"],
        "slack": ["post_message"],
    }
    post = m.tool("slack.post_message")
    assert post is not None and post.effect == "write"
    orders = m.tool("shop.list_orders")
    assert orders is not None and orders.schema_hash
    assert orders.input_schema["required"] == ["since", "until"]
    assert m.result_schemas[0]["required"] == ["end", "start"]
    assert m.budgets.max_calls == 12 and m.budgets.max_llm_tokens == 20000
    assert m.volatile_paths[1] and m.usable is False and m.warnings == ["w"]
    assert Manifest.model_validate(m.model_dump(mode="json")) == m
