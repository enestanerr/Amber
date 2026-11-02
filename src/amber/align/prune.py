"""Dead-step elimination (``docs/04-compiler.md`` §A5).

A step is pruned when it is read-only, nothing downstream references its result, it is
optional (absent from ≥ 1 trace) and it is not the final step. Pruning runs to a fixpoint so
a read that only fed another pruned read goes too. Core unreferenced read-only steps are kept
but marked ``observe`` (a judgment step may want their result as context). Calls that failed
in every trace and feed nothing are pruned as well.
"""

from __future__ import annotations

import re

from amber.align.models import StepSpec
from amber.align.provenance import call_at
from amber.trace.models import Trace

READ_NAME_RE = re.compile(r"^(get|list|search|read|fetch|find|lookup)", re.IGNORECASE)
BUILTIN_READ = frozenset({"builtin.Read", "builtin.Glob", "builtin.Grep", "builtin.WebFetch"})


def is_read_only(step: StepSpec) -> bool:
    if step.effect == "read" or step.tool in BUILTIN_READ:
        return True
    _, _, name = step.tool.partition(".")
    return bool(READ_NAME_RE.match(name))


def _all_errors(step: StepSpec, traces: dict[str, Trace]) -> bool:
    return all(call_at(traces[tid], idx).is_error for tid, idx in step.calls.items())


def _referenced(steps: list[StepSpec]) -> set[int]:
    out: set[int] = set()
    for step in steps:
        out.update(step.referenced_steps())
    return out


def prune_steps(
    steps: list[StepSpec],
    traces: dict[str, Trace],
    *,
    keep_unreferenced: bool,
    warnings: list[str],
) -> tuple[list[StepSpec], list[StepSpec]]:
    """Return ``(kept, pruned)``; kept steps are renumbered and their refs remapped."""
    n_traces = len(traces)
    last = steps[-1] if steps else None
    remaining = list(steps)
    pruned: list[StepSpec] = []
    while True:
        referenced = _referenced(remaining)
        newly: list[StepSpec] = []
        for step in remaining:
            unreferenced = step.idx not in referenced
            optional = len(step.present_in) < n_traces
            if unreferenced and _all_errors(step, traces):
                step.pruned_reason = "failed in every trace and unreferenced"
                newly.append(step)
            elif (
                unreferenced
                and optional
                and step is not last
                and is_read_only(step)
                and not keep_unreferenced
            ):
                step.pruned_reason = (
                    f"read-only, unreferenced, present {len(step.present_in)}/{n_traces}"
                )
                newly.append(step)
        if not newly:
            break
        pruned.extend(newly)
        remaining = [s for s in remaining if s not in newly]

    referenced = _referenced(remaining)
    for step in remaining:
        unreferenced = step.idx not in referenced
        core = len(step.present_in) == n_traces
        step.observe = unreferenced and core and step is not last and is_read_only(step)

    mapping = {step.idx: new for new, step in enumerate(remaining)}
    for step in remaining:
        step.args = {path: prov.remap(mapping) for path, prov in step.args.items()}
        if step.predicate is not None and step.predicate.col is not None:
            step.predicate = step.predicate.model_copy(
                update={"col": mapping.get(step.predicate.col, step.predicate.col)}
            )
        step.idx = mapping[step.idx]
    pruned.sort(key=lambda s: s.idx)
    for step in pruned:
        warnings.append(f"pruned: step {step.idx} {step.tool} ({step.pruned_reason})")
    return remaining, pruned
