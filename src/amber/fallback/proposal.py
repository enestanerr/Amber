"""Fallback proposal (``docs/05-runtime.md`` §5): what a stopped run suggests should happen."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from amber.compile.manifest import Manifest
from amber.runtime.runner import RunResult

Action = Literal["retry", "fallback_resume", "fallback_full", "abort"]

# stop kind → the action the arbiter table proposes first
PROPOSED: dict[str, Action] = {
    "error": "retry",
    "schema_drift": "fallback_resume",
    "result_shape": "fallback_resume",
    "llm_output": "fallback_resume",
    "budget": "abort",
    "allowlist": "abort",
    "lint": "abort",
}


class FallbackProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow: str
    version: int
    step_idx: int
    tool: str
    stop_kind: str
    stop_detail: str
    completed_steps: list[int] = Field(default_factory=list[int])
    completed_write_steps: list[int] = Field(default_factory=list[int])
    remaining_write_steps: list[int] = Field(default_factory=list[int])
    proposed_action: Action
    retried: bool = False  # the stop already happened after a retry


def build_proposal(
    result: RunResult, manifest: Manifest, *, retried: bool = False
) -> FallbackProposal:
    """Derive the proposal from a stopped :class:`RunResult` and the manifest's step table."""
    stop_kind = result.stop_kind or "error"
    step_idx = max(
        (s.idx for s in result.step_log if s.status in ("stopped", "error")), default=None
    )
    if step_idx is None:
        step_idx = max((s.idx for s in result.step_log), default=-1) + 1
    tool = next((s.tool for s in manifest.steps if s.idx == step_idx), "?")
    completed = sorted({s.idx for s in result.step_log if s.status in ("ok", "dry-run")})
    effects = {s.idx: s.effect for s in manifest.steps}
    completed_writes = [i for i in completed if effects.get(i) not in (None, "read")]
    remaining_writes = [s.idx for s in manifest.steps if s.idx >= step_idx and s.effect != "read"]
    action = PROPOSED.get(stop_kind, "fallback_resume")
    if action == "retry" and retried:
        action = "fallback_resume"
    return FallbackProposal(
        run_id=result.run_id,
        workflow=result.workflow,
        version=result.version,
        step_idx=step_idx,
        tool=tool,
        stop_kind=stop_kind,
        stop_detail=result.stop_detail or (result.error or ""),
        completed_steps=completed,
        completed_write_steps=completed_writes,
        remaining_write_steps=remaining_writes,
        proposed_action=action,
        retried=retried,
    )
