"""Sequence alignment of tool-call sequences (``docs/04-compiler.md`` §A2).

Pairwise Needleman-Wunsch on canonical tool names: match ``+2`` (``+1`` more when the args
fingerprint is equal), mismatch ``-1``, gap ``-1``; ties prefer the diagonal, then a gap in
the shorter sequence. Progressive multi-alignment uses the longest trace as the anchor and
aligns every other trace against the anchor's column list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MATCH = 2
FINGERPRINT_BONUS = 1
MISMATCH = -1
GAP = -1


@dataclass(frozen=True)
class CallKey:
    """What alignment sees of a call: its tool and its args fingerprint."""

    tool: str
    fingerprint: str


@dataclass
class Column:
    """One aligned step: ``trace_id → call index`` (position among the trace's calls)."""

    calls: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def traces(self) -> list[str]:
        return list(self.calls)


@dataclass
class Alignment:
    anchor: str
    columns: list[Column]
    warnings: list[str] = field(default_factory=list[str])

    def is_core(self, column: Column, trace_ids: list[str]) -> bool:
        return all(t in column.calls for t in trace_ids)


def _score(a: CallKey, b: CallKey) -> int:
    if a.tool != b.tool:
        return MISMATCH
    return MATCH + (FINGERPRINT_BONUS if a.fingerprint == b.fingerprint else 0)


def align_pair(a: list[CallKey], b: list[CallKey]) -> list[tuple[int | None, int | None]]:
    """Global alignment of *a* and *b*; returns ``(i | None, j | None)`` pairs in order."""
    n, m = len(a), len(b)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = i * GAP
    for j in range(1, m + 1):
        score[0][j] = j * GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = score[i - 1][j - 1] + _score(a[i - 1], b[j - 1])
            up = score[i - 1][j] + GAP  # gap in b (consume a)
            left = score[i][j - 1] + GAP  # gap in a (consume b)
            score[i][j] = max(diag, up, left)
    # Traceback. Tie order: diagonal first, then the gap in the shorter sequence, i.e. consume
    # an element of the longer sequence first.
    prefer_up = n >= m
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and score[i][j] == score[i - 1][j - 1] + _score(a[i - 1], b[j - 1]):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
            continue
        up_ok = i > 0 and score[i][j] == score[i - 1][j] + GAP
        left_ok = j > 0 and score[i][j] == score[i][j - 1] + GAP
        if up_ok and (prefer_up or not left_ok):
            pairs.append((i - 1, None))
            i -= 1
        elif left_ok:
            pairs.append((None, j - 1))
            j -= 1
        else:  # pragma: no cover - defensive; DP guarantees one of the moves matches
            raise RuntimeError("alignment traceback failed")
    pairs.reverse()
    return pairs


def _column_key(column: Column, keys: dict[str, list[CallKey]], anchor: str) -> CallKey:
    if anchor in column.calls:
        return keys[anchor][column.calls[anchor]]
    trace_id, idx = next(iter(column.calls.items()))
    return keys[trace_id][idx]


def align_traces(keys: dict[str, list[CallKey]]) -> Alignment:
    """Progressive alignment of several call sequences (``trace_id → keys``)."""
    if not keys:
        raise ValueError("no traces to align")
    order = list(keys)
    anchor = max(order, key=lambda t: (len(keys[t]), -order.index(t)))
    columns = [Column({anchor: i}) for i in range(len(keys[anchor]))]
    warnings: list[str] = []
    for trace_id in order:
        if trace_id == anchor:
            continue
        col_keys = [_column_key(c, keys, anchor) for c in columns]
        merged: list[Column] = []
        for ci, cj in align_pair(col_keys, keys[trace_id]):
            if ci is not None and cj is not None:
                if col_keys[ci].tool != keys[trace_id][cj].tool:
                    # NW may pair different tools (one mismatch beats two gaps); a column
                    # holds exactly one tool, so split the pair into two columns.
                    merged.append(columns[ci])
                    merged.append(Column({trace_id: cj}))
                    continue
                columns[ci].calls[trace_id] = cj
                merged.append(columns[ci])
            elif ci is not None:
                merged.append(columns[ci])
            else:
                assert cj is not None
                merged.append(Column({trace_id: cj}))
        columns = merged
    alignment = Alignment(anchor=anchor, columns=columns, warnings=warnings)
    _merge_reordered(alignment, keys)
    _warn_repeated(alignment, keys)
    return alignment


def _merge_reordered(alignment: Alignment, keys: dict[str, list[CallKey]]) -> None:
    """``A,B`` vs ``B,A``: NW leaves two single-trace columns for the same tool next to each
    other; merge them into one core column and keep the anchor order (v0.1)."""
    columns = alignment.columns
    trace_ids = list(keys)
    changed = True
    while changed:
        changed = False
        for i, col in enumerate(columns):
            if alignment.is_core(col, trace_ids):
                continue
            for j in range(max(0, i - 2), min(len(columns), i + 3)):
                other = columns[j]
                if j == i or set(col.calls) & set(other.calls):
                    continue
                tool_i = _column_key(col, keys, alignment.anchor).tool
                tool_j = _column_key(other, keys, alignment.anchor).tool
                if tool_i != tool_j:
                    continue
                keep, drop = (col, other) if alignment.anchor in col.calls else (other, col)
                keep.calls.update(drop.calls)
                columns.remove(drop)
                alignment.warnings.append(
                    f"reordered_steps: {tool_i} appears in a different order across traces;"
                    " keeping the anchor order"
                )
                changed = True
                break
            if changed:
                break


def _warn_repeated(alignment: Alignment, keys: dict[str, list[CallKey]]) -> None:
    run_tool: str | None = None
    run_len = 0
    for col in alignment.columns:
        tool = _column_key(col, keys, alignment.anchor).tool
        if tool == run_tool:
            run_len += 1
        else:
            if run_len >= 3:
                alignment.warnings.append(
                    f"repeated_call: {run_tool} x{run_len} in a row; loop detection is planned"
                    " for v0.2 (steps are kept as separate columns)"
                )
            run_tool, run_len = tool, 1
    if run_len >= 3:
        alignment.warnings.append(
            f"repeated_call: {run_tool} x{run_len} in a row; loop detection is planned"
            " for v0.2 (steps are kept as separate columns)"
        )
