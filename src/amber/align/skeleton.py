"""``build_skeleton``: align → provenance → classify → prune → context hints, inferred
inputs and volatile paths (``docs/04-compiler.md`` §A). Models live in ``align.models``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from amber.align.classify import classify_steps
from amber.align.models import (
    InputSpec,
    Predicate,
    Provenance,
    Skeleton,
    StepSpec,
    TemplatePart,
)
from amber.align.provenance import call_at, mine_provenance
from amber.align.prune import prune_steps
from amber.align.sequence import CallKey, align_traces
from amber.trace.canonical import args_fingerprint
from amber.trace.jsonpath import Wildcard, format_path, iter_leaves, parse_path
from amber.trace.models import Thought, ToolCall, Trace

__all__ = [
    "InputSpec",
    "Predicate",
    "Provenance",
    "Skeleton",
    "StepSpec",
    "TemplatePart",
    "build_skeleton",
    "generalize_path",
    "thoughts_before",
]

THOUGHTS_LIMIT = 2000
CONTEXT_HINT_MAX = 8
MIN_CONTEXT_TOKEN = 4


def thoughts_before(trace: Trace) -> dict[int, str]:
    """``call event idx → concatenated Thought text since the previous call`` (≤ 2000 chars)."""
    out: dict[int, str] = {}
    buffer: list[str] = []
    for event in trace.events:
        if isinstance(event, Thought):
            buffer.append(event.text)
        elif isinstance(event, ToolCall):
            text = "\n".join(buffer)
            out[event.idx] = text[-THOUGHTS_LIMIT:] if len(text) > THOUGHTS_LIMIT else text
            buffer = []
    return out


def generalize_path(path: str) -> str:
    """``$.orders[3].id`` → ``$.orders[*].id``."""
    parts = tuple(Wildcard() if isinstance(p, int) else p for p in parse_path(path))
    return format_path(parts)


def _call_keys(trace: Trace) -> list[CallKey]:
    return [CallKey(tool=c.tool, fingerprint=args_fingerprint(c.args)) for c in trace.calls]


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _provisional_steps(traces: Sequence[Trace]) -> tuple[list[StepSpec], list[str]]:
    by_id = {t.id: t for t in traces}
    alignment = align_traces({t.id: _call_keys(t) for t in traces})
    steps: list[StepSpec] = []
    for idx, column in enumerate(alignment.columns):
        calls = {tid: by_id[tid].calls[pos].idx for tid, pos in column.calls.items()}
        anchor_tid = alignment.anchor if alignment.anchor in calls else next(iter(calls))
        ordered = {
            anchor_tid: calls[anchor_tid],
            **{t: c for t, c in calls.items() if t != anchor_tid},
        }
        anchor_call = call_at(by_id[anchor_tid], ordered[anchor_tid])
        schema = by_id[anchor_tid].tool_schema(anchor_call.tool)
        steps.append(
            StepSpec(
                idx=idx,
                tool=anchor_call.tool,
                present_in=list(ordered),
                calls=ordered,
                effect=schema.effect if schema else "unknown",
                schema_hash=schema.hash if schema else "",
                thoughts={
                    tid: thoughts_before(by_id[tid]).get(ci, "") for tid, ci in ordered.items()
                },
            )
        )
    return steps, list(alignment.warnings)


def _volatile_paths(step: StepSpec, traces: dict[str, Trace], used: set[str]) -> list[str]:
    """Generalized result paths whose values differ across traces and feed no later step."""
    if len(step.calls) < 2:
        return []
    per_path: dict[str, list[tuple[str, ...]]] = {}
    for tid, event_idx in step.calls.items():
        values: dict[str, list[str]] = {}
        for path, value in iter_leaves(call_at(traces[tid], event_idx).result):
            values.setdefault(generalize_path(path), []).append(repr(value))
        for gpath, vals in values.items():
            per_path.setdefault(gpath, []).append(tuple(vals))
    return [g for g, seen in sorted(per_path.items()) if g not in used and len(set(seen)) > 1]


def _context_hints(step: StepSpec, kept: list[StepSpec], traces: dict[str, Trace]) -> list[int]:
    """Earlier steps whose results the args reference or whose content the judgment echoes."""
    hints: list[int] = sorted(step.referenced_steps())
    if step.kind != "judgment":
        return hints[:CONTEXT_HINT_MAX]
    anchor_tid = next(iter(step.calls))
    anchor_args = call_at(traces[anchor_tid], step.calls[anchor_tid]).args
    unknown_text = " ".join(
        str(v)
        for p, v in iter_leaves(anchor_args)
        if p in step.args and step.args[p].kind == "unknown" and isinstance(v, str)
    )
    thoughts = " ".join(step.thoughts.values()).lower()
    for earlier in kept:
        if earlier.idx >= step.idx or earlier.idx in hints:
            continue
        name_tokens = [t for t in re.split(r"[._\-]", earlier.tool.split(".", 1)[1]) if len(t) >= 3]
        mentioned = any(tok.lower() in thoughts for tok in name_tokens)
        echoed = False
        if anchor_tid in earlier.calls and unknown_text:
            result = call_at(traces[anchor_tid], earlier.calls[anchor_tid]).result
            echoed = any(
                isinstance(v, str) and len(v) >= MIN_CONTEXT_TOKEN and v in unknown_text
                for _, v in iter_leaves(result)
            )
        if mentioned or echoed:
            hints.append(earlier.idx)
    return sorted(hints)[:CONTEXT_HINT_MAX]


def build_skeleton(
    traces: Sequence[Trace],
    *,
    keep_unreferenced: bool = False,
) -> Skeleton:
    """Align → provenance → classify → prune → hints, inferred inputs and volatile paths."""
    if not traces:
        raise ValueError("build_skeleton needs at least one trace")
    workflow = traces[0].workflow
    by_id = {t.id: t for t in traces}
    steps, warnings = _provisional_steps(traces)

    inputs: dict[str, InputSpec] = {}
    for trace in traces:
        for name, value in trace.inputs.items():
            spec = inputs.setdefault(name, InputSpec(type=_json_type(value), source="declared"))
            if value not in spec.examples:
                spec.examples.append(value)

    mine_provenance(steps, by_id, warnings)
    classify_steps(steps, by_id, inputs, warnings)
    kept, pruned = prune_steps(steps, by_id, keep_unreferenced=keep_unreferenced, warnings=warnings)

    edges: list[tuple[int, int]] = []
    used_paths: set[tuple[int, str]] = set()
    for step in kept:
        for src in sorted(step.referenced_steps()):
            if (src, step.idx) not in edges:
                edges.append((src, step.idx))
        for prov in step.args.values():
            used_paths.update(prov.ref_paths())
        if step.predicate and step.predicate.kind == "data" and step.predicate.col is not None:
            used_paths.add((step.predicate.col, step.predicate.path or "$"))
        step.context_hint = _context_hints(step, kept, by_id)

    volatile: dict[int, list[str]] = {}
    for step in kept:
        used = {generalize_path(p) for c, p in used_paths if c == step.idx}
        paths = _volatile_paths(step, by_id, used)
        if paths:
            volatile[step.idx] = paths

    skeleton = Skeleton(
        workflow=workflow,
        traces=[t.id for t in traces],
        inputs=inputs,
        steps=kept,
        pruned=pruned,
        edges=edges,
        volatile_paths=volatile,
        warnings=warnings,
    )
    skeleton.hash = skeleton.compute_hash()
    return skeleton
