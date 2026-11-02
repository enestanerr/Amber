"""Classification table (§A4), inferred inputs (§A6) and pruning (§A5)."""

from __future__ import annotations

from typing import Any

import pytest

from amber.align.skeleton import build_skeleton
from amber.trace.models import Trace
from tests.unit.align.conftest import mk_trace


def _post_traces(
    texts: list[str], *, thoughts: str | None = None, extra: dict[str, Any] | None = None
) -> list[Any]:
    out: list[Any] = []
    for i, text in enumerate(texts):
        args = {"channel": "#eng", "text": text, **(extra or {})}
        out.append(
            mk_trace(
                f"t{i}",
                [("shop.list_orders", {}, [{"id": i}]), ("slack.post_message", args, {"ok": True})],
                thoughts={1: thoughts} if thoughts else None,
            )
        )
    return out


def test_long_varying_string_is_judgment() -> None:
    texts = [f"Weekly summary number {i}: all good" for i in range(3)]  # ≥ 24 chars
    s = build_skeleton(_post_traces(texts))
    step = s.steps[1]
    assert step.kind == "judgment" and step.args["$.text"].hole == "judgment"
    assert step.args["$.channel"].kind == "const"


def test_short_varying_string_becomes_inferred_input() -> None:
    s = build_skeleton(_post_traces(["alpha", "beta", "gamma"]))
    step = s.steps[1]
    assert step.kind == "deterministic"
    assert step.args["$.text"].kind == "input" and step.args["$.text"].name == "text"
    assert s.inputs["text"].source == "inferred" and s.inputs["text"].examples == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert any(w.startswith("inferred_input") for w in s.warnings)


def test_enum_like_labels_are_judgment() -> None:
    s = build_skeleton(_post_traces(["bug", "bug", "ui"]))
    assert s.steps[1].kind == "judgment" and s.steps[1].args["$.text"].hole == "judgment"


def test_decision_cue_in_thoughts_makes_short_string_judgment() -> None:
    s = build_skeleton(
        _post_traces(["alpha", "beta", "gamma"], thoughts="I think the best channel is eng")
    )
    assert s.steps[1].kind == "judgment"


def test_date_and_numeric_unknowns_are_transforms() -> None:
    traces = [
        mk_trace(
            f"t{i}",
            [("shop.week", {}, {"start": start}), ("shop.rfm", {"as_of": as_of, "n": n}, [])],
        )
        for i, (start, as_of, n) in enumerate(
            [
                ("2026-10-12", "2026-10-11", 3),
                ("2026-10-19", "2026-10-18", 4),
                ("2026-10-26", "2026-10-25", 5),
            ]
        )
    ]
    step = build_skeleton(traces).steps[1]
    assert step.kind == "transform-needed"
    assert step.args["$.as_of"].hole == "transform" and step.args["$.n"].hole == "transform"


def test_iso_week_and_month_unknowns_are_transforms_not_inferred_inputs() -> None:
    """The previous ISO week (W42 -> W41) is derived from the input, not a new input."""
    traces = [
        mk_trace(
            f"t{i}",
            [("shop.week_range", {"week": prev, "month": month}, {"start": "x"})],
            inputs={"week": week},
        )
        for i, (week, prev, month) in enumerate(
            [("2026-W42", "2026-W41", "2026-09"), ("2026-W43", "2026-W42", "2026-10")]
        )
    ]
    s = build_skeleton(traces)
    assert s.steps[0].kind == "transform-needed"
    assert s.steps[0].args["$.week"].hole == "transform"
    assert s.steps[0].args["$.month"].hole == "transform"
    assert [n for n, spec in s.inputs.items() if spec.source == "inferred"] == []


@pytest.mark.parametrize(
    ("value", "date_like"),
    [
        ("2026-10-12", True),
        ("2026-10-12T07:23:00Z", True),
        ("2026-W41", True),
        ("2026-W41-3", True),
        ("2026-285", True),
        ("2026-10", True),
        ("2026-W8", False),
        ("LNT-011", False),
        ("eng", False),
    ],
)
def test_is_date_like(value: str, date_like: bool) -> None:
    from amber.align.classify import is_date_like

    assert is_date_like(value) is date_like


def test_mixed_leaves_judgment_wins_for_step_kind() -> None:
    traces = [
        mk_trace(
            f"t{i}",
            [("a.b", {"text": f"a long judgment text number {i} here", "score": i * 1.5}, None)],
        )
        for i in range(3)
    ]
    step = build_skeleton(traces).steps[0]
    assert step.kind == "judgment"
    assert step.args["$.text"].hole == "judgment" and step.args["$.score"].hole == "transform"


def _conditional_traces(counts: list[int], *, post_when: int) -> list[Any]:
    out: list[Any] = []
    for i, n in enumerate(counts):
        calls: list[Any] = [
            ("shop.count", {}, {"count": n, "label": "hot" if n > post_when else "cold"})
        ]
        if n > post_when:
            calls.append(("slack.post", {"text": "alert: threshold exceeded today"}, {"ok": True}))
        calls.append(("shop.done", {}, None))
        out.append(mk_trace(f"t{i}", calls))
    return out


def test_conditional_step_gets_data_predicate() -> None:
    s = build_skeleton(_conditional_traces([0, 5, 9], post_when=2))
    post = s.steps[1]
    assert post.tool == "slack.post" and post.conditional
    assert post.predicate is not None and post.predicate.kind == "data"
    assert post.predicate.col == 0 and post.predicate.path in ("$.label", "$.count")
    assert (0, 1) in s.edges


def test_conditional_step_without_separator_gets_gate() -> None:
    traces = [
        mk_trace(
            "t0",
            [
                ("shop.count", {}, {"count": 1}),
                ("slack.post", {"text": "alert now please"}, {"ok": 1}),
                ("shop.done", {}, None),
            ],
        ),
        mk_trace("t1", [("shop.count", {}, {"count": 1}), ("shop.done", {}, None)]),
    ]
    post = build_skeleton(traces).steps[1]
    assert post.conditional and post.predicate is not None and post.predicate.kind == "unknown"


def _page_traces(posts: list[bool], snapshots: list[str]) -> list[Trace]:
    out = []
    for i, (post, snap) in enumerate(zip(posts, snapshots, strict=True)):
        calls: list[Any] = [("web.snapshot", {}, snap)]
        if post:
            calls.append(
                ("alerts.post", {"text": f"prices changed on day {i}, see list"}, {"ok": 1})
            )
        calls.append(("web.close", {}, None))
        out.append(mk_trace(f"t{i}", calls))
    return out


def test_whole_page_text_never_becomes_a_predicate() -> None:
    """A snapshot that differs per run "separates" any split; only a gate can decide."""
    pages = [f"- listitem: Brass Lantern ${48 + i}.00 in stock: {12 - i} " * 4 for i in range(3)]
    post = build_skeleton(_page_traces([False, True, True], pages)).steps[1]
    assert post.conditional and post.predicate is not None and post.predicate.kind == "unknown"
    # identical long text on the present side is content, not a flag
    same = [pages[0], pages[1], pages[1]]
    post = build_skeleton(_page_traces([False, True, True], same)).steps[1]
    assert post.predicate is not None and post.predicate.kind == "unknown"


def test_equality_predicate_needs_two_supporting_traces() -> None:
    one_present = build_skeleton(_page_traces([True, False, False], ["a", "b", "c"])).steps[1]
    assert one_present.predicate is not None and one_present.predicate.kind == "unknown"
    flags = build_skeleton(_page_traces([True, True, False], ["changed", "changed", "same"]))
    post = flags.steps[1]
    assert post.predicate is not None and post.predicate.kind == "data"
    assert (post.predicate.op, post.predicate.value) == ("==", "changed")


def test_pruning_rules() -> None:
    base: list[Any] = [
        ("shop.list_orders", {}, [{"id": 1}]),
        ("slack.post", {"text": "hello world from the test"}, {"ok": 1}),
    ]
    with_read = [base[0], ("builtin.Read", {"file_path": "x"}, "txt"), base[1]]
    with_peek = [base[0], ("shop.get_customer", {"id": 1}, {"name": "x"}), base[1]]
    s = build_skeleton([mk_trace("t0", with_read), mk_trace("t1", base), mk_trace("t2", with_peek)])
    tools = [st.tool for st in s.steps]
    assert "builtin.Read" not in tools and "shop.get_customer" not in tools
    assert [p.tool for p in s.pruned] == ["builtin.Read", "shop.get_customer"]
    assert s.steps[0].observe is True  # core unreferenced read-only → observe
    assert s.steps[-1].observe is False
    kept = build_skeleton([mk_trace("t0", with_read), mk_trace("t1", base)], keep_unreferenced=True)
    assert "builtin.Read" in [st.tool for st in kept.steps]


def test_optional_write_and_last_read_are_not_pruned() -> None:
    a = [
        ("shop.list", {}, []),
        ("slack.post", {"t": "x"}, {"ok": 1}),
        ("shop.get_status", {}, {"s": 1}),
    ]
    b = [("shop.list", {}, []), ("shop.get_status", {}, {"s": 1})]
    s = build_skeleton([mk_trace("t0", a), mk_trace("t1", b)])
    assert [st.tool for st in s.steps] == ["shop.list", "slack.post", "shop.get_status"]
    assert s.steps[1].conditional and s.pruned == []


def test_failed_unreferenced_calls_are_pruned() -> None:
    calls = [("shop.list", {}, "permission denied"), ("slack.post", {"t": "x"}, {"ok": 1})]
    s = build_skeleton([mk_trace("t0", calls, errors=[0]), mk_trace("t1", calls, errors=[0])])
    assert [st.tool for st in s.steps] == ["slack.post"]
    assert s.pruned[0].pruned_reason is not None and "failed" in s.pruned[0].pruned_reason
