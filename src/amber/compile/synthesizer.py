"""Hole synthesis with structured outputs and a bounded repair loop
(``docs/04-compiler.md`` §D2-D3).

For every hole: ask the model (``TransformFill`` / ``JudgmentFill``), verify against the
recordings, feed the exact mismatch back up to ``max_repair`` times. A transform that still
fails is **downgraded to a judgment hole** (warning recorded); a judgment that fails raises
:class:`CompileError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from amber.align.models import Skeleton
from amber.compile.holes import (
    Hole,
    VerifyReport,
    downgrade_to_judgment,
    shrink_to_bytes,
    verify_judgment,
    verify_transform,
)
from amber.compile.manifest import FieldSpec
from amber.compile.prompts import GATE_RULES, JUDGMENT_RULES, SYNTHESIZER_SYSTEM, TRANSFORM_RULES
from amber.errors import AmberError
from amber.models.backend import ModelBackend
from amber.trace.canonical import canonical_json
from amber.trace.models import Usage
from amber.util.json import dumps_canonical

MAX_TOKENS = 4000
MISMATCH_CHARS = 1024


class CompileError(AmberError):
    exit_code = 1


class TransformFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python_source: str = Field(description="Complete Python source defining the function.")
    rationale: str = Field(description="One paragraph explaining the approach.")


class JudgmentFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(description="The runtime prompt (≤ 1200 chars).")
    fields: list[FieldSpec] = Field(description="Output fields (one per expected key).")
    rationale: str = Field(description="One paragraph explaining the approach.")


@dataclass
class HoleOutcome:
    hole: Hole
    fill: TransformFill | JudgmentFill | None
    attempts: int
    accepted: bool
    downgraded: bool = False
    report: VerifyReport | None = None
    notes: list[str] = field(default_factory=list[str])
    usage: Usage = field(default_factory=Usage)

    @property
    def rationale(self) -> str:
        return self.fill.rationale if self.fill is not None else ""


def _mismatch_payload(report: VerifyReport) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in report.mismatches:
        entry: dict[str, Any] = {"trace": m.get("trace")}
        for key in ("expected", "actual"):
            if key in m:
                entry[key] = canonical_json(m[key])[:MISMATCH_CHARS]
        for key in ("exception", "error"):
            if key in m:
                entry[key] = m[key]
        out.append(entry)
    return out


def user_message(
    hole: Hole,
    *,
    attempt: int,
    previous: BaseModel | None,
    report: VerifyReport | None,
) -> str:
    """JSON user message: hole kind, signature, examples (shrunk), expected outputs, repair info."""
    payload: dict[str, Any] = {
        "hole_id": hole.id,
        "kind": hole.kind,
        "tool": hole.tool,
        "attempt": attempt,
    }
    if hole.kind == "transform":
        payload["signature"] = hole.signature
        payload["produces"] = {n: hole.expected_types[n] for n in hole.fields}
        payload["examples"] = [
            {
                "trace": ex.trace_id,
                "params": {k: shrink_to_bytes(v) for k, v in ex.params.items()},
                "expected": ex.expected,
            }
            for ex in hole.examples
        ]
        payload["rules"] = TRANSFORM_RULES
    else:
        payload["schema_name"] = hole.schema_name
        payload["expected_fields"] = {n: hole.expected_types[n] for n in hole.fields}
        payload["context_keys"] = hole.context_keys
        payload["examples"] = [
            {
                "trace": ex.trace_id,
                "context": {k: shrink_to_bytes(v) for k, v in ex.context.items()},
                "expected": ex.expected,
                "agent_thoughts": ex.thoughts[:600],
            }
            for ex in hole.examples
        ]
        payload["rules"] = GATE_RULES if hole.kind == "gate" else JUDGMENT_RULES
    if previous is not None and report is not None:
        payload["previous_attempt"] = previous.model_dump(mode="json")
        payload["mismatch"] = {"details": report.details, "cases": _mismatch_payload(report)}
    return dumps_canonical(payload)


def _verify(hole: Hole, fill: BaseModel, forbidden: list[str]) -> VerifyReport:
    if hole.kind == "transform":
        assert isinstance(fill, TransformFill)
        return verify_transform(hole, fill.python_source)
    assert isinstance(fill, JudgmentFill)
    return verify_judgment(hole, fill.prompt, fill.fields, forbidden_terms=forbidden)


async def fill_hole(
    hole: Hole,
    backend: ModelBackend,
    *,
    model: str,
    max_repair: int,
    forbidden: list[str],
) -> HoleOutcome:
    schema: type[BaseModel] = TransformFill if hole.kind == "transform" else JudgmentFill
    previous: BaseModel | None = None
    report: VerifyReport | None = None
    usage = Usage()
    for attempt in range(max_repair + 1):
        message = user_message(hole, attempt=attempt, previous=previous, report=report)
        fill, used = await backend.complete(
            schema, SYNTHESIZER_SYSTEM, message, model=model, max_tokens=MAX_TOKENS
        )
        usage = usage + used
        report = _verify(hole, fill, forbidden)
        if report.ok:
            assert isinstance(fill, TransformFill | JudgmentFill)
            return HoleOutcome(
                hole, fill, attempt + 1, True, report=report, notes=list(report.notes), usage=usage
            )
        previous = fill
    assert isinstance(previous, TransformFill | JudgmentFill)
    return HoleOutcome(hole, previous, max_repair + 1, False, report=report, usage=usage)


async def synthesize(
    holes: list[Hole],
    skeleton: Skeleton,
    backend: ModelBackend,
    *,
    model: str,
    max_repair: int = 3,
    warnings: list[str],
) -> list[HoleOutcome]:
    """Fill every hole; mutate *skeleton* when a transform is downgraded to a judgment."""
    forbidden = [s.tool for s in skeleton.steps] + list(skeleton.traces)
    outcomes: list[HoleOutcome] = []
    for hole in holes:
        outcome = await fill_hole(
            hole, backend, model=model, max_repair=max_repair, forbidden=forbidden
        )
        if outcome.accepted:
            outcomes.append(outcome)
            continue
        if hole.kind == "transform":
            detail = outcome.report.summary() if outcome.report else "no report"
            warnings.append(f"transform_downgraded: {hole.id} → judgment ({detail})")
            judgment = downgrade_to_judgment(hole)
            step = skeleton.steps[hole.step_idx]
            step.kind = "judgment"
            for path in hole.fields.values():
                step.args[path].hole = "judgment"
            retry = await fill_hole(
                judgment, backend, model=model, max_repair=max_repair, forbidden=forbidden
            )
            retry.downgraded = True
            retry.usage = retry.usage + outcome.usage
            if not retry.accepted:
                raise CompileError(
                    f"hole {judgment.id} could not be filled after {retry.attempts} attempts: "
                    f"{retry.report.summary() if retry.report else 'no report'}",
                    hint="Record another run with clearer inputs, or edit the prompt "
                    "in program.py by hand.",
                )
            outcomes.append(retry)
            continue
        raise CompileError(
            f"hole {hole.id} could not be filled after {outcome.attempts} attempts: "
            f"{outcome.report.summary() if outcome.report else 'no report'}",
            hint="Record another run with clearer inputs, or edit the prompt "
            "in program.py by hand.",
        )
    return outcomes
