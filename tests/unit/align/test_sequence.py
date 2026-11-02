"""Needleman-Wunsch alignment cases from docs/04-compiler.md §A2."""

from __future__ import annotations

from amber.align.sequence import CallKey, align_pair, align_traces


def K(tool: str, fp: str = "x") -> CallKey:  # tiny test helper
    return CallKey(tool=tool, fingerprint=fp)


def test_identical_sequences_are_all_core() -> None:
    seq = [K("a"), K("b"), K("c")]
    al = align_traces({"t1": seq, "t2": list(seq), "t3": list(seq)})
    assert len(al.columns) == 3
    assert all(al.is_core(c, ["t1", "t2", "t3"]) for c in al.columns)
    assert al.warnings == []


def test_extra_exploratory_read_becomes_optional_column() -> None:
    base = [K("shop.list"), K("slack.post")]
    extra = [K("shop.list"), K("builtin.Read"), K("slack.post")]
    al = align_traces({"t1": base, "t2": extra, "t3": list(base)})
    assert al.anchor == "t2"  # longest trace anchors
    assert [sorted(c.calls) for c in al.columns] == [["t1", "t2", "t3"], ["t2"], ["t1", "t2", "t3"]]
    assert al.columns[1].calls == {"t2": 1}


def test_reordered_independent_reads_merge_into_core_columns_with_warning() -> None:
    al = align_traces({"t1": [K("a"), K("b"), K("c")], "t2": [K("b"), K("a"), K("c")]})
    assert len(al.columns) == 3
    assert all(al.is_core(c, ["t1", "t2"]) for c in al.columns)
    assert al.columns[0].calls == {"t1": 0, "t2": 1}  # anchor order kept
    assert any(w.startswith("reordered_steps") for w in al.warnings)


def test_repeated_calls_keep_separate_columns_and_warn() -> None:
    seq = [
        K("shop.list_orders", "1"),
        K("shop.list_orders", "2"),
        K("shop.list_orders", "3"),
        K("x"),
    ]
    al = align_traces({"t1": seq, "t2": list(seq)})
    assert len(al.columns) == 4
    assert any("repeated_call" in w for w in al.warnings)


def test_align_pair_prefers_fingerprint_match() -> None:
    a = [K("list", "A"), K("list", "B")]
    b = [K("list", "B")]
    pairs = align_pair(a, b)
    assert pairs == [(0, None), (1, 0)]


def test_align_pair_gap_in_shorter_sequence() -> None:
    assert align_pair([K("a"), K("b")], []) == [(0, None), (1, None)]
    assert align_pair([], [K("a")]) == [(None, 0)]
    assert align_pair([K("a"), K("z"), K("b")], [K("a"), K("b")]) == [(0, 0), (1, None), (2, 1)]


def test_single_trace() -> None:
    al = align_traces({"only": [K("a"), K("b")]})
    assert al.anchor == "only" and [c.calls for c in al.columns] == [{"only": 0}, {"only": 1}]
