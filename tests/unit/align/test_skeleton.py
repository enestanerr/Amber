"""End-to-end skeleton on the weekly-report fixtures (P3 acceptance shape)."""

from __future__ import annotations

from amber.align.models import Skeleton
from amber.align.skeleton import build_skeleton, generalize_path, thoughts_before
from amber.trace.models import Trace


def test_weekly_report_skeleton_shape(weekly_traces: list[Trace]) -> None:
    s = build_skeleton(weekly_traces)
    assert s.workflow == "weekly-report" and s.traces == ["t1", "t2", "t3"]
    assert [st.tool for st in s.steps] == [
        "shop.week_range",
        "shop.list_orders",
        "shop.top_products",
        "shop.rfm_snapshot",
        "slack.post_message",
    ]
    assert [st.kind for st in s.steps] == [
        "deterministic",
        "deterministic",
        "deterministic",
        "transform-needed",
        "judgment",
    ]
    assert [p.tool for p in s.pruned] == ["builtin.Read"]
    assert all(len(st.present_in) == 3 for st in s.steps)
    assert s.inputs["week"].source == "declared" and s.inputs["week"].examples == [
        "2026-W42",
        "2026-W43",
        "2026-W44",
    ]


def test_weekly_report_provenance_and_edges(weekly_traces: list[Trace]) -> None:
    s = build_skeleton(weekly_traces)
    wr, lo, tp, rfm, post = s.steps
    assert wr.args["$.week"].describe() == "Input(week)"
    assert (
        lo.args["$.since"].describe() == "Ref(0,$.start)"
        and lo.args["$.until"].describe() == "Ref(0,$.end)"
    )
    assert tp.args["$.n"].kind == "const" and tp.args["$.n"].value == 5
    assert rfm.args["$.as_of"].kind == "unknown" and rfm.args["$.as_of"].hole == "transform"
    assert post.args["$.channel"].value == "#eng" and post.args["$.text"].hole == "judgment"
    assert s.edges == [(0, 1), (0, 2)]
    assert rfm.observe is True  # core, read-only, unreferenced → kept for context
    assert post.effect == "write" and wr.effect == "read"
    assert all(st.schema_hash for st in s.steps)
    assert (
        2 in post.context_hint and 3 in post.context_hint
    )  # product names echoed, snapshot mentioned


def test_weekly_report_volatile_paths_and_hash(weekly_traces: list[Trace]) -> None:
    s = build_skeleton(weekly_traces)
    assert "$[*].created_at" in s.volatile_paths[1] and "$[*].id" in s.volatile_paths[1]
    assert 0 not in s.volatile_paths  # start/end are referenced downstream
    assert len(s.hash) == 16
    again = build_skeleton(weekly_traces)
    assert again.hash == s.hash
    assert Skeleton.model_validate(s.model_dump(mode="json")).hash == s.hash


def test_inferred_week_input_when_not_declared(weekly_traces: list[Trace]) -> None:
    undeclared = [t.model_copy(update={"inputs": {}}) for t in weekly_traces]
    s = build_skeleton(undeclared)
    assert s.inputs["week"].source == "inferred"
    assert s.steps[0].args["$.week"].describe() == "Input(week)"
    assert s.steps[1].args["$.since"].describe() == "Ref(0,$.start)"
    assert [st.kind for st in s.steps] == [
        "deterministic",
        "deterministic",
        "deterministic",
        "transform-needed",
        "judgment",
    ]


def test_thoughts_before_and_generalize_path(weekly_traces: list[Trace]) -> None:
    t2 = weekly_traces[1]
    tb = thoughts_before(t2)
    first_call = t2.calls[0].idx
    assert tb[first_call].startswith("I'll resolve the week range")
    assert tb[t2.calls[1].idx] == ""
    assert tb[t2.calls[-1].idx].startswith("Comparing the snapshot")
    assert generalize_path("$.orders[3].items[0].sku") == "$.orders[*].items[*].sku"
    assert generalize_path("$.start") == "$.start"
