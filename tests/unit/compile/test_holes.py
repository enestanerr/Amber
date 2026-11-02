"""Hole collection and verification (docs/04-compiler.md §D1)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from amber.align.models import Skeleton
from amber.compile.holes import (
    Hole,
    HoleExample,
    build_judgment_model,
    collect_holes,
    downgrade_to_judgment,
    field_name,
    infer_field_type,
    shrink,
    shrink_to_bytes,
    values_equal,
    verify_judgment,
    verify_transform,
)
from amber.compile.manifest import FieldSpec
from amber.compile.synthesizer import JudgmentFill, TransformFill
from amber.trace.models import Trace


def _walk_additional_properties(schema: dict[str, Any]) -> list[str]:
    missing: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            d: dict[str, Any] = node
            if d.get("type") == "object" and d.get("additionalProperties") is not False:
                missing.append(path)
            for k, v in d.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):  # type: ignore[arg-type]
                walk(v, f"{path}[{i}]")

    walk(schema, "$")
    return missing


def test_weekly_holes(weekly_skeleton: Skeleton, weekly_traces: list[Trace]) -> None:
    holes = collect_holes(weekly_skeleton, weekly_traces)
    assert [h.id for h in holes] == ["transform_step3", "judgment_step4"]
    t, j = holes
    assert t.params == ["inputs", "r0", "r1", "r2"] and t.fields == {"as_of": "$.as_of"}
    assert t.signature.startswith("def transform_step3(inputs: dict[str, Any], r0: Any")
    assert [ex.expected["as_of"] for ex in t.examples] == ["2026-10-11", "2026-10-18", "2026-10-25"]
    assert t.examples[0].params["r0"] == {"start": "2026-10-12", "end": "2026-10-18"}
    assert t.expected_types == {"as_of": "str"}
    assert j.fields == {"text": "$.text"}
    assert j.context_keys == ["inputs", "r0", "r1", "r2", "r3"]  # observe steps + mentioned
    assert (
        j.schema_name == "Step4Out" and j.prompt_name == "PROMPT_step4" and j.llm_step_id == "step4"
    )
    assert j.examples[1].context["inputs"] == {"week": "2026-W43"}
    assert all(len(ex.expected["text"]) > 100 for ex in j.examples)
    assert "summar" in j.examples[0].thoughts


def test_field_name_and_types() -> None:
    assert field_name("$.as_of") == "as_of"
    assert field_name("$.filter.since") == "filter_since"
    assert field_name("$.labels[0]") == "labels_0"
    assert field_name("$.9x") == "v_9x"
    assert infer_field_type([True, False]) == "bool"
    assert infer_field_type([1, 2]) == "int"
    assert infer_field_type([1, 2.5]) == "float"
    assert infer_field_type([["a"], []]) == "list[str]"
    assert infer_field_type(["x", 1]) == "str"


def test_shrink() -> None:
    big = {"items": list(range(50)), "text": "x" * 1000, "nested": {"k": [1]}}
    small = shrink(big)
    assert len(small["items"]) == 21 and small["items"][-1] == "...+30"
    assert len(small["text"]) == 501 and small["nested"] == {"k": [1]}
    huge = [{"pad": "y" * 400} for _ in range(100)]
    assert len(str(shrink_to_bytes(huge))) < 9000


def test_values_equal_rules() -> None:
    notes: list[str] = []
    assert values_equal(1, 1.0 + 1e-12, notes)
    assert not values_equal(1, 1.1, notes)
    assert values_equal("a ", "a", notes)
    assert not values_equal(True, 1, notes)
    assert values_equal([{"a": 1}, {"a": 2}], [{"a": 2}, {"a": 1}], notes) and notes
    assert not values_equal([1, 2], [1], notes)
    assert values_equal({"x": [1, {"y": 2.0}]}, {"x": [1, {"y": 2}]}, notes)
    assert not values_equal({"x": 1}, {"y": 1}, notes)


def _hole(**kw: Any) -> Hole:
    base: dict[str, Any] = {
        "id": "transform_step1",
        "kind": "transform",
        "step_idx": 1,
        "tool": "shop.t",
        "fields": {"limit": "$.limit"},
        "params": ["inputs", "r0"],
        "context_keys": ["inputs", "r0"],
        "examples": [
            HoleExample("t0", params={"inputs": {}, "r0": {"count": 2}}, expected={"limit": 4}),
            HoleExample("t1", params={"inputs": {}, "r0": {"count": 5}}, expected={"limit": 10}),
        ],
        "expected_types": {"limit": "int"},
    }
    base.update(kw)
    return Hole(**base)


def test_verify_transform_accepts_and_rejects() -> None:
    good = "def transform_step1(inputs, r0):\n    return {'limit': r0['count'] * 2}\n"
    ok = verify_transform(_hole(), good)
    assert ok.ok and ok.mismatches == []
    wrong = "def transform_step1(inputs, r0):\n    return {'limit': r0['count'] + 2}\n"
    bad = verify_transform(_hole(), wrong)
    assert not bad.ok and bad.mismatches[0]["trace"] == "t1"  # 2 + 2 == 4 still passes for t0
    assert "mismatch at limit" in bad.details[0]
    raising = "def transform_step1(inputs, r0):\n    return {'limit': r0['nope']}\n"
    exc = verify_transform(_hole(), raising)
    assert not exc.ok and "KeyError" in exc.details[0]
    not_dict = "def transform_step1(inputs, r0):\n    return 4\n"
    assert "expected a dict" in verify_transform(_hole(), not_dict).details[0]
    forbidden = "import os\ndef transform_step1(inputs, r0):\n    return {'limit': 4}\n"
    assert "disallowed" in verify_transform(_hole(), forbidden).details[0]


def _judgment() -> Hole:
    return Hole(
        id="judgment_step1",
        kind="judgment",
        step_idx=1,
        tool="github.add_labels",
        fields={"label": "$.labels[0]"},
        params=[],
        context_keys=["inputs", "r0"],
        examples=[
            HoleExample("t0", context={"inputs": {}, "r0": {}}, expected={"label": "bug"}),
            HoleExample("t1", context={"inputs": {}, "r0": {}}, expected={"label": "ui"}),
        ],
        expected_types={"label": "str"},
    )


def test_verify_judgment_rules() -> None:
    fields = [FieldSpec(name="label", type="str", description="d", enum=["bug", "ui"])]
    ok = verify_judgment(_judgment(), "Pick a label using `r0` and `inputs`.", fields)
    assert ok.ok
    assert not verify_judgment(_judgment(), "x" * 1201, fields).ok
    assert not verify_judgment(_judgment(), "", fields).ok
    bad_key = verify_judgment(_judgment(), "Look at `r7` and {orders}.", fields)
    assert not bad_key.ok and "unavailable context keys" in bad_key.details[0]
    leak = verify_judgment(
        _judgment(), "Call github.add_labels", fields, forbidden_terms=["github.add_labels"]
    )
    assert not leak.ok and "leaks" in leak.details[0]
    wrong_fields = verify_judgment(_judgment(), "ok", [FieldSpec(name="other")])
    assert not wrong_fields.ok and "fields must be exactly" in wrong_fields.details[0]
    narrow = [FieldSpec(name="label", type="str", enum=["bug"])]
    rejected = verify_judgment(_judgment(), "Pick.", narrow)
    assert not rejected.ok and rejected.mismatches[0]["trace"] == "t1"


def test_judgment_model_and_structured_output_schemas_forbid_extras() -> None:
    model = build_judgment_model(
        "StepXOut", [FieldSpec(name="label", enum=["a", "b"]), FieldSpec(name="n", type="int")]
    )
    inst = model.model_validate({"label": "a", "n": 1, "confidence": 0.5})
    assert inst.model_dump() == {"label": "a", "n": 1, "confidence": 0.5}
    with pytest.raises(ValidationError):
        model.model_validate({"label": "zzz", "n": 1, "confidence": 0.5})
    for m in (model, TransformFill, JudgmentFill, FieldSpec):
        assert _walk_additional_properties(m.model_json_schema()) == [], m.__name__


def test_downgrade_to_judgment() -> None:
    j = downgrade_to_judgment(_hole())
    assert j.id == "judgment_step1" and j.kind == "judgment" and j.fields == {"limit": "$.limit"}
    assert j.context_keys == ["inputs", "r0"] and j.examples[0].context["r0"] == {"count": 2}
    assert j.examples[1].expected == {"limit": 10}
