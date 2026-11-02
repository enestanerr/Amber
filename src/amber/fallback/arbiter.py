"""Arbiter: proposal + vetoes → decision, written to ``decision_records`` before anything acts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from amber.fallback.proposal import FallbackProposal
from amber.fallback.sentry import Veto
from amber.runtime.ledger import Ledger


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["retry", "fallback_resume", "abort"]
    reason: str
    actor: Literal["arbiter", "user"] = "arbiter"


def arbitrate(proposal: FallbackProposal, vetoes: list[Veto]) -> Decision:
    """Apply the decision table, then the vetoes (a vetoed action degrades to abort)."""
    blocked = {b for v in vetoes for b in v.blocks}

    def vetoed_by(action: str) -> Veto | None:
        return next((v for v in vetoes if action in v.blocks), None)

    wanted = proposal.proposed_action
    if wanted == "fallback_full":  # v0.1 never re-runs the whole task; resume instead
        wanted = "fallback_resume"
    if wanted == "retry":
        if "retry" in blocked:
            veto = vetoed_by("retry")
            assert veto is not None
            return Decision(action="abort", reason=f"retry vetoed by {veto.rule}: {veto.reason}")
        return Decision(
            action="retry",
            reason=f"transient tool error at step {proposal.step_idx + 1}: retry once",
        )
    if wanted == "fallback_resume":
        if "fallback_resume" in blocked:
            veto = vetoed_by("fallback_resume")
            assert veto is not None
            return Decision(action="abort", reason=f"fallback vetoed by {veto.rule}: {veto.reason}")
        return Decision(
            action="fallback_resume",
            reason=f"{proposal.stop_kind} at step {proposal.step_idx + 1} ({proposal.tool}): "
            "the agent resumes from there with the completed results as context",
        )
    return Decision(action="abort", reason=f"{proposal.stop_kind}: {proposal.stop_detail[:160]}")


def record(
    ledger: Ledger, proposal: FallbackProposal, vetoes: list[Veto], decision: Decision
) -> None:
    ledger.add_decision(
        proposal.run_id,
        proposal.workflow,
        proposal=proposal.model_dump(mode="json"),
        vetoes=[v.model_dump(mode="json") for v in vetoes],
        decision=decision.action,
        reason=decision.reason,
        actor=decision.actor,
    )
