"""Provenance kinds, priority and the consistency rule (docs/04-compiler.md §A3)."""

from __future__ import annotations

from amber.align.skeleton import build_skeleton
from tests.unit.align.conftest import mk_trace


def _prov(skeleton_steps, idx: int, path: str):  # type: ignore[no-untyped-def]
    return skeleton_steps[idx].args[path]


def test_input_wins_over_ref_when_both_match() -> None:
    traces = [
        mk_trace(
            f"t{i}",
            [
                ("shop.week_range", {"week": w}, {"week": w, "start": s}),
                ("shop.list", {"w": w}, []),
            ],
            inputs={"week": w},
        )
        for i, (w, s) in enumerate([("2026-W42", "2026-10-12"), ("2026-W43", "2026-10-19")])
    ]
    s = build_skeleton(traces)
    p = _prov(s.steps, 1, "$.w")
    assert p.kind == "input" and p.name == "week"


def test_ref_to_earlier_result_first_column_then_shortest_path() -> None:
    traces = [
        mk_trace(
            f"t{i}",
            [
                ("shop.week_range", {"week": w}, {"start": s, "nested": {"deep": {"start": s}}}),
                ("shop.copy", {}, {"again": s}),
                ("shop.list_orders", {"since": s}, []),
            ],
            inputs={"week": w},
        )
        for i, (w, s) in enumerate([("W42", "2026-10-12"), ("W43", "2026-10-19")])
    ]
    s = build_skeleton(traces)
    p = _prov(s.steps, 2, "$.since")
    assert p.kind == "ref" and p.col == 0 and p.path == "$.start"
    assert (0, 2) in s.edges and (1, 2) not in s.edges


def test_template_from_input_and_literal_remainder() -> None:
    traces = [
        mk_trace(f"t{i}", [("shop.search", {"q": f"week={w}"}, [])], inputs={"week": w})
        for i, w in enumerate(["2026-W42", "2026-W43", "2026-W44"])
    ]
    p = _prov(build_skeleton(traces).steps, 0, "$.q")
    assert p.kind == "template"
    assert [(x.literal, x.ref.name if x.ref else None) for x in p.parts or []] == [
        ("week=", None),
        (None, "week"),
    ]


def test_template_needs_three_literal_chars() -> None:
    traces = [
        mk_trace(f"t{i}", [("shop.search", {"q": f"{w}Z"}, [])], inputs={"week": w})
        for i, w in enumerate(["2026-10-12", "2026-10-19"])
    ]
    s = build_skeleton(traces)
    p = _prov(s.steps, 0, "$.q")
    # one literal char is not a template; the short varying string is promoted to an input
    assert p.kind == "input" and p.name == "q" and s.inputs["q"].source == "inferred"


def test_const_and_unknown_large() -> None:
    big = "x" * (20 * 1024 + 1)
    traces = [
        mk_trace(f"t{i}", [("slack.post", {"channel": "#eng", "text": f"{big}{i}"}, {"ok": True})])
        for i in range(2)
    ]
    steps = build_skeleton(traces).steps
    assert (
        _prov(steps, 0, "$.channel").kind == "const"
        and _prov(steps, 0, "$.channel").value == "#eng"
    )
    t = _prov(steps, 0, "$.text")
    assert t.kind == "unknown" and t.large is True and t.hole == "judgment"


def test_consistency_rule_two_of_three_is_unknown() -> None:
    def trace(i: int, since: str, start: str) -> object:
        return mk_trace(
            f"t{i}",
            [
                ("shop.week_range", {"week": "w"}, {"start": start}),
                ("shop.list", {"since": since}, []),
            ],
        )

    traces = [
        trace(0, "2026-10-12", "2026-10-12"),
        trace(1, "2026-10-19", "2026-10-19"),
        trace(2, "2026-10-26", "1999-01-01"),
    ]
    p = _prov(build_skeleton(traces).steps, 1, "$.since")  # type: ignore[arg-type]
    assert p.kind == "unknown" and p.hole == "transform"  # date-like → transform hole


def test_numbers_compare_numerically_and_nested_args_flatten() -> None:
    traces = [
        mk_trace(
            f"t{i}", [("shop.count", {}, {"n": n}), ("shop.top", {"filter": {"limit": lim}}, [])]
        )
        for i, (n, lim) in enumerate([(5, 5.0), (7, 7)])
    ]
    s = build_skeleton(traces)
    p = _prov(s.steps, 1, "$.filter.limit")
    assert p.kind == "ref" and p.col == 0 and p.path == "$.n"


def test_single_trace_everything_is_const_or_ref() -> None:
    s = build_skeleton(
        [mk_trace("t0", [("a.get", {"k": "v"}, {"x": 1}), ("a.put", {"x": 1, "k": "v"}, None)])]
    )
    assert _prov(s.steps, 0, "$.k").kind == "const"
    assert _prov(s.steps, 1, "$.x").kind == "ref"
    assert all(st.kind == "deterministic" for st in s.steps)


def test_constant_value_prefers_const_over_a_list_position_ref() -> None:
    """``n=5`` in every run must not become ``Ref(0,$.rows[1].f)`` because row 1 has ``f=5``."""
    traces = [
        mk_trace(
            f"t{i}",
            [
                ("shop.rfm", {}, {"rows": [{"id": f"c{i}", "f": 2}, {"id": "c9", "f": 5}]}),
                ("shop.top", {"n": 5, "first": f"c{i}"}, []),
            ],
        )
        for i in range(3)
    ]
    s = build_skeleton(traces)
    assert _prov(s.steps, 1, "$.n").describe() == "Const(5)"
    # a value that varies with the run is still explained by the list position
    first = _prov(s.steps, 1, "$.first")
    assert first.kind == "ref" and first.path == "$.rows[0].id"
