"""``REPORT.md`` rendering (skeleton table, hole fills, verify table, warnings)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from amber.align.models import Skeleton, StepSpec
from amber.compile.synthesizer import HoleOutcome

TEMPLATES = Path(__file__).parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    undefined=StrictUndefined,
    trim_blocks=False,
    lstrip_blocks=False,
    keep_trailing_newline=True,
    autoescape=False,
)


def provenance_text(step: StepSpec) -> str:
    bits = [f"{p[2:] if p.startswith('$.') else p} ← {v.describe()}" for p, v in step.args.items()]
    if step.predicate is not None:
        bits.insert(0, f"if {step.predicate.describe()}")
    return " · ".join(bits).replace("|", "\\|") if bits else "(no args)"


def render_report(
    skeleton: Skeleton,
    *,
    version: int,
    created_at: datetime,
    program_hash: str,
    usable: bool,
    outcomes: list[HoleOutcome],
    verify_rows: list[dict[str, Any]] | None = None,
    warnings: list[str],
) -> str:
    steps = [
        {
            "idx": s.idx,
            "tool": s.tool,
            "kind": s.kind,
            "conditional": s.conditional,
            "observe": s.observe,
            "effect": s.effect,
            "provenance": provenance_text(s),
            "present": f"{len(s.present_in)}/{len(skeleton.traces)}",
        }
        for s in skeleton.steps
    ]
    pruned = [{"tool": p.tool, "reason": p.pruned_reason or ""} for p in skeleton.pruned]
    holes = [
        {
            "id": o.hole.id,
            "kind": o.hole.kind,
            "accepted": o.accepted,
            "attempts": o.attempts,
            "downgraded": o.downgraded,
            "rationale": o.rationale,
            "notes": o.notes,
        }
        for o in outcomes
    ]
    return _ENV.get_template("report.md.j2").render(
        workflow=skeleton.workflow,
        version=version,
        created_at=created_at.isoformat(timespec="seconds"),
        traces=skeleton.traces,
        skeleton_hash=skeleton.hash,
        program_hash=program_hash,
        usable=usable,
        steps=steps,
        pruned=pruned,
        holes=holes,
        verify_rows=verify_rows or [],
        warnings=warnings,
    )
