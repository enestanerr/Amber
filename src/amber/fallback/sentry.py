"""Sentry: veto rules evaluated against a proposal (``docs/05-runtime.md`` §5).

Every rule is evaluated; a veto blocks the named action(s), not necessarily all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from amber.fallback.proposal import Action, FallbackProposal
from amber.runtime.ledger import Ledger

FALLBACKS: tuple[Action, ...] = ("fallback_resume", "fallback_full")


class Veto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: str
    reason: str
    blocks: list[Action]


@dataclass(frozen=True)
class SentryContext:
    fallback_enabled: bool  # --fallback agent given
    allow_writes: bool
    daily_budget: int
    fallbacks_today: int
    lint_failed: bool = False


def fallbacks_today(ledger: Ledger, workflow: str, now: datetime) -> int:
    """Fallback decisions recorded today (UTC date) for this workflow."""
    day = now.date().isoformat()
    return sum(
        1
        for d in ledger.decisions(workflow, n=500)
        if str(d["ts"]).startswith(day) and str(d["decision"]).startswith("fallback")
    )


def evaluate(proposal: FallbackProposal, ctx: SentryContext) -> list[Veto]:
    vetoes: list[Veto] = []
    if not ctx.fallback_enabled:
        vetoes.append(
            Veto(
                rule="no_fallback_flag",
                reason="run started with --fallback none",
                blocks=list(FALLBACKS),
            )
        )
    if ctx.fallbacks_today >= ctx.daily_budget:
        vetoes.append(
            Veto(
                rule="daily_budget",
                reason=f"{ctx.fallbacks_today} fallbacks today ≥ budget {ctx.daily_budget}",
                blocks=list(FALLBACKS),
            )
        )
    if proposal.completed_write_steps:
        vetoes.append(
            Veto(
                rule="writes_already_done",
                reason=f"write steps {proposal.completed_write_steps} already executed; "
                "re-running from scratch would duplicate side effects",
                blocks=["fallback_full"],
            )
        )
    if (
        proposal.stop_kind in ("server", "connection")
        or "unreachable" in proposal.stop_detail.lower()
    ):
        vetoes.append(
            Veto(
                rule="servers_unreachable",
                reason="a server is unreachable; the agent would fail too",
                blocks=list(FALLBACKS),
            )
        )
    if proposal.stop_kind == "budget":
        vetoes.append(
            Veto(rule="budget_exhausted", reason="the run exhausted a budget", blocks=["retry"])
        )
    if ctx.lint_failed or proposal.stop_kind == "lint":
        vetoes.append(
            Veto(rule="lint_failure", reason="program.py fails lint", blocks=["retry", *FALLBACKS])
        )
    if not ctx.allow_writes and proposal.remaining_write_steps:
        vetoes.append(
            Veto(
                rule="writes_not_allowed",
                reason=f"remaining steps {proposal.remaining_write_steps} write and the run "
                "did not allow writes; the agent might perform them",
                blocks=list(FALLBACKS),
            )
        )
    return vetoes
