"""``amber run --fallback agent``: proposal → sentry → arbiter → ledger → action
(``docs/05-runtime.md`` §5). Nothing acts before its decision is in ``decision_records``."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from amber.adapters.base import RecordRequest
from amber.adapters.claude_stream import StreamRecording
from amber.cli._output import Output
from amber.compile.versions import VersionInfo
from amber.config import AmberConfig
from amber.errors import AmberError
from amber.fallback.agent_fallback import build_request, record_fallback
from amber.fallback.arbiter import Decision, arbitrate, record
from amber.fallback.proposal import FallbackProposal, build_proposal
from amber.fallback.sentry import SentryContext, Veto, evaluate, fallbacks_today
from amber.models.backend import ModelBackend
from amber.runtime.ledger import Ledger
from amber.runtime.runner import RunResult, run_program
from amber.trace.models import ToolCall
from amber.trace.store import TraceStore
from amber.util.paths import amber_home
from amber.util.time import utcnow

Recorder = Callable[..., Coroutine[Any, Any, StreamRecording]]


@dataclasses.dataclass
class FallbackOutcome:
    proposal: FallbackProposal
    vetoes: list[Veto]
    decision: Decision
    result: RunResult
    trace_id: str | None = None


def _decide(
    ledger: Ledger,
    cfg: AmberConfig,
    result: RunResult,
    manifest_steps: Any,
    *,
    allow_writes: bool,
    retried: bool,
) -> tuple[FallbackProposal, list[Veto], Decision]:
    proposal = build_proposal(result, manifest_steps, retried=retried)
    ctx = SentryContext(
        fallback_enabled=True,
        allow_writes=allow_writes,
        daily_budget=cfg.fallback.daily_budget,
        fallbacks_today=fallbacks_today(ledger, result.workflow, utcnow()),
    )
    vetoes = evaluate(proposal, ctx)
    decision = arbitrate(proposal, vetoes)
    record(ledger, proposal, vetoes, decision)
    return proposal, vetoes, decision


def _print_chain(
    out: Output, proposal: FallbackProposal, vetoes: list[Veto], decision: Decision
) -> None:
    out.print(
        f"[bold]fallback[/] proposal: {proposal.proposed_action} "
        f"(step {proposal.step_idx + 1} {proposal.tool}, {proposal.stop_kind})"
    )
    for v in vetoes:
        out.print(f"  veto {v.rule}: {v.reason} → blocks {', '.join(v.blocks)}")
    out.print(f"  decision: [bold]{decision.action}[/] — {decision.reason}")


def run_fallback(
    root: Path,
    cfg: AmberConfig,
    info: VersionInfo,
    result: RunResult,
    *,
    inputs: dict[str, Any],
    allow_writes: bool,
    out: Output,
    llm_backend: ModelBackend | None = None,
    recorder: Recorder = record_fallback,
) -> RunResult:
    """Decide and act on a stopped run; returns the final :class:`RunResult`."""
    manifest = info.manifest()
    if manifest is None:
        raise AmberError(f"{info.path} has no manifest.json")
    ledger = Ledger(amber_home(root) / "amber.db")
    store = TraceStore(amber_home(root))
    outcome = decide_and_act(
        root, cfg, info, result,
        inputs=inputs, allow_writes=allow_writes, out=out, llm_backend=llm_backend,
        recorder=recorder, ledger=ledger, store=store,
    )  # fmt: skip
    return outcome.result


def decide_and_act(
    root: Path,
    cfg: AmberConfig,
    info: VersionInfo,
    result: RunResult,
    *,
    inputs: dict[str, Any],
    allow_writes: bool,
    out: Output,
    llm_backend: ModelBackend | None,
    recorder: Recorder,
    ledger: Ledger,
    store: TraceStore,
) -> FallbackOutcome:
    manifest = info.manifest()
    assert manifest is not None
    proposal, vetoes, decision = _decide(
        ledger, cfg, result, manifest, allow_writes=allow_writes, retried=False
    )
    _print_chain(out, proposal, vetoes, decision)
    current = result
    if decision.action == "retry":
        current = run_program(
            root, cfg, info, inputs, allow_writes=allow_writes, llm_backend=llm_backend
        )
        if current.status != "stopped":
            return FallbackOutcome(proposal, vetoes, decision, current)
        proposal, vetoes, decision = _decide(
            ledger, cfg, current, manifest, allow_writes=allow_writes, retried=True
        )
        _print_chain(out, proposal, vetoes, decision)
    if decision.action != "fallback_resume":
        return FallbackOutcome(proposal, vetoes, decision, current)

    task_prompt = _task_prompt(store, manifest.workflow)
    request: RecordRequest = build_request(
        manifest, proposal,
        inputs=inputs, completed=current.completed_calls, task_prompt=task_prompt,
        allow_writes=allow_writes, cwd=root,
    )  # fmt: skip
    out.print(f"  handing the task back to the agent from step {proposal.step_idx + 1} …")
    rec = asyncio.run(
        recorder(request, resumed_from_step=proposal.step_idx, prefix=current.completed_calls)
    )
    path = store.save(rec.trace)
    turns = rec.trace.outcome.num_turns or 0
    ledger.set_agent_turns(current.run_id, turns)
    final = dataclasses.replace(
        current,
        status="ok" if rec.trace.outcome.status == "success" else "failed",
        agent_turns=turns,
        output=None,
        error=None if rec.trace.outcome.status == "success" else "agent fallback did not succeed",
    )
    out.print(
        f"  fallback trace [bold]{rec.trace.id}[/] saved "
        f"({len(rec.trace.calls)} calls, {turns} agent turns) → {path}"
    )
    out.print(
        f"  next: `amber compile {manifest.workflow} --refresh` to fold it into a new version"
    )
    return FallbackOutcome(proposal, vetoes, decision, final, trace_id=rec.trace.id)


def _task_prompt(store: TraceStore, workflow: str) -> str:
    latest = store.latest(workflow)
    return (
        latest.task_prompt
        if latest is not None and latest.task_prompt
        else f"complete the {workflow} workflow"
    )


__all__ = ["FallbackOutcome", "ToolCall", "decide_and_act", "run_fallback"]
