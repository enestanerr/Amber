"""Provenance mining (``docs/04-compiler.md`` §A3).

For every leaf of every step's args resolve, in priority order, the first kind that holds in
**every** trace where the step is present: ``Input`` → ``Ref`` → ``Template`` → ``Const`` →
``Unknown``. Comparisons are type-aware (numbers compare numerically, strings exactly except
trailing whitespace).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from amber.align.models import Provenance, StepSpec, TemplatePart
from amber.trace.canonical import canonical_json
from amber.trace.jsonpath import MISSING, get_path, iter_leaves
from amber.trace.models import ToolCall, Trace

LARGE_BYTES = 20 * 1024
MIN_TEMPLATE_VALUE = 3  # candidate values shorter than this never form templates
MIN_TEMPLATE_LITERAL = 3  # total literal characters a template must keep

Candidate = tuple[Provenance, str]


def leaf_key(value: Any) -> tuple[str, str]:
    """Type-aware equality key: ints and floats compare numerically, strings exactly."""
    if isinstance(value, bool):
        return ("bool", repr(value))
    if isinstance(value, int | float):
        return ("number", repr(float(value)))
    if isinstance(value, str):
        return ("str", value.rstrip())
    return ("json", canonical_json(value))


@dataclass
class TraceIndex:
    """Per-trace lookup tables filled step by step (only earlier steps are visible)."""

    trace: Trace
    inputs: dict[str, Any]
    raw: dict[int, Any] = field(default_factory=dict[int, Any])  # step idx → result
    # step idx → leaf key → result paths holding that value (shortest first)
    results: dict[int, dict[tuple[str, str], list[str]]] = field(
        default_factory=dict[int, dict[tuple[str, str], list[str]]]
    )
    # step idx → [(path, str(value))] for string/number leaves usable in templates
    strings: dict[int, list[tuple[str, str]]] = field(
        default_factory=dict[int, list[tuple[str, str]]]
    )

    def register(self, step_idx: int, result: Any) -> None:
        by_key: dict[tuple[str, str], list[str]] = {}
        strings: list[tuple[str, str]] = []
        for path, value in iter_leaves(result):
            by_key.setdefault(leaf_key(value), []).append(path)
            if isinstance(value, str | int | float) and not isinstance(value, bool):
                text = str(value)
                if len(text) >= MIN_TEMPLATE_VALUE:
                    strings.append((path, text))
        for paths in by_key.values():
            paths.sort(key=lambda p: (len(p), p))
        self.raw[step_idx] = result
        self.results[step_idx] = by_key
        self.strings[step_idx] = strings


Index = dict[str, TraceIndex]


def call_at(trace: Trace, event_idx: int) -> ToolCall:
    call = trace.events[event_idx]
    assert isinstance(call, ToolCall)
    return call


def _resolve_input(values: dict[str, Any], index: Index, step_idx: int) -> Provenance | None:
    names: set[str] | None = None
    for tid, value in values.items():
        key = leaf_key(value)
        hits = {
            n for n, v in index[tid].inputs.items() if leaf_key(v) == key or str(v) == str(value)
        }
        names = hits if names is None else names & hits
        if not names:
            return None
    if not names:
        return None
    return Provenance(kind="input", name=sorted(names)[0])


def _resolve_ref(values: dict[str, Any], index: Index, step_idx: int) -> Provenance | None:
    common: set[tuple[int, str]] | None = None
    for tid, value in values.items():
        key = leaf_key(value)
        hits: set[tuple[int, str]] = set()
        for col, table in index[tid].results.items():
            if col >= step_idx:
                continue
            for path in table.get(key, []):
                hits.add((col, path))
        common = hits if common is None else common & hits
        if not common:
            return None
    if not common:
        return None
    col, path = min(common, key=lambda cp: (cp[0], len(cp[1]), cp[1]))
    return Provenance(kind="ref", col=col, path=path)


def _candidates(tid: str, index: Index, step_idx: int) -> list[Candidate]:
    out: list[Candidate] = []
    for name, value in index[tid].inputs.items():
        text = str(value)
        if len(text) >= MIN_TEMPLATE_VALUE:
            out.append((Provenance(kind="input", name=name), text))
    for col, strings in index[tid].strings.items():
        if col >= step_idx:
            continue
        for path, text in strings:
            out.append((Provenance(kind="ref", col=col, path=path), text))
    out.sort(key=lambda c: (-len(c[1]), c[0].kind != "input", c[0].col or -1, c[0].path or ""))
    return out


def _decompose(text: str, candidates: list[Candidate]) -> list[TemplatePart] | None:
    parts: list[TemplatePart] = []
    i = 0
    literal = ""
    refs = 0
    while i < len(text):
        hit: Candidate | None = None
        for prov, value in candidates:  # longest first
            if text.startswith(value, i):
                hit = (prov, value)
                break
        if hit is None:
            literal += text[i]
            i += 1
            continue
        if literal:
            parts.append(TemplatePart(literal=literal))
            literal = ""
        parts.append(TemplatePart(ref=hit[0]))
        refs += 1
        i += len(hit[1])
    if literal:
        parts.append(TemplatePart(literal=literal))
    if refs == 0:
        return None
    literal_total = sum(len(p.literal) for p in parts if p.literal is not None)
    if literal_total < MIN_TEMPLATE_LITERAL:
        return None
    return parts


def render_template(parts: list[TemplatePart], tid: str, index: Index) -> str | None:
    """Render a template for one trace; ``None`` when a referenced value is missing."""
    out = ""
    for part in parts:
        if part.literal is not None:
            out += part.literal
            continue
        ref = part.ref
        assert ref is not None
        if ref.kind == "input":
            if ref.name not in index[tid].inputs:
                return None
            out += str(index[tid].inputs[ref.name])
        else:
            assert ref.col is not None and ref.path is not None
            if ref.col not in index[tid].raw:
                return None
            value = get_path(index[tid].raw[ref.col], ref.path)
            if value is MISSING:
                return None
            out += str(value)
    return out


def _resolve_template(values: dict[str, Any], index: Index, step_idx: int) -> Provenance | None:
    sample_tid, sample = next(iter(values.items()))
    if not isinstance(sample, str) or len(sample) <= MIN_TEMPLATE_VALUE:
        return None
    parts = _decompose(sample, _candidates(sample_tid, index, step_idx))
    if parts is None:
        return None
    for tid, value in values.items():
        if not isinstance(value, str) or render_template(parts, tid, index) != value:
            return None
    return Provenance(kind="template", parts=parts)


def _resolve_const(values: dict[str, Any]) -> Provenance | None:
    keys = {leaf_key(v) for v in values.values()}
    if len(keys) == 1:
        return Provenance(kind="const", value=next(iter(values.values())))
    return None


def through_list_position(prov: Provenance) -> bool:
    """A ``Ref`` (or a template part) that reaches its value through a list index."""
    if prov.kind == "ref":
        return "[" in (prov.path or "")
    if prov.kind == "template":
        return any(p.ref is not None and through_list_position(p.ref) for p in prov.parts or [])
    return False


def resolve(values: dict[str, Any], index: Index, step_idx: int) -> Provenance:
    """Provenance of one arg leaf given its value in every trace where the step exists."""
    const = _resolve_const(values)
    for finder in (_resolve_input, _resolve_ref, _resolve_template):
        prov = finder(values, index, step_idx)
        if prov is not None:
            # identical in every trace: a list position explains the value no better than the
            # literal and breaks when the list reorders (live run: ``top_products n=5`` matched
            # an unrelated ``rfm_snapshot $.result[22].f == 5`` in all three recordings)
            if const is not None and through_list_position(prov):
                return const
            return prov
    if const is not None:
        return const
    large = any(len(canonical_json(v)) >= LARGE_BYTES for v in values.values())
    return Provenance(kind="unknown", large=large)


def build_index(traces: dict[str, Trace]) -> Index:
    return {tid: TraceIndex(trace=t, inputs=dict(t.inputs)) for tid, t in traces.items()}


def mine_provenance(steps: list[StepSpec], traces: dict[str, Trace], warnings: list[str]) -> Index:
    """Fill ``step.args`` for every step in order; returns the index (results per trace)."""
    index = build_index(traces)
    for step in steps:
        anchor_tid = next(iter(step.calls))
        anchor_call = call_at(traces[anchor_tid], step.calls[anchor_tid])
        shape_warned = False
        for path, _ in iter_leaves(anchor_call.args):
            values: dict[str, Any] = {}
            for tid, event_idx in step.calls.items():
                value = get_path(call_at(traces[tid], event_idx).args, path)
                if value is MISSING:
                    if not shape_warned:
                        warnings.append(f"arg_shape_varies: step {step.idx} {step.tool} {path}")
                        shape_warned = True
                    continue
                values[tid] = value
            if len(values) < len(step.calls):
                step.args[path] = Provenance(kind="unknown")
                continue
            step.args[path] = resolve(values, index, step.idx)
        for tid, event_idx in step.calls.items():
            index[tid].register(step.idx, call_at(traces[tid], event_idx).result)
    return index
