"""Runner: live execution of a program version with guards and the ledger
(``docs/05-runtime.md`` §1).

``Context`` is a sync facade: the runner owns an event loop in a worker thread; ``ctx.tool``
submits a coroutine and blocks on its future, so programs stay simple synchronous code.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from amber.compile.lint import lint_program
from amber.compile.manifest import Manifest
from amber.compile.versions import VersionInfo
from amber.config import AmberConfig
from amber.errors import AmberError, UsageError
from amber.models.backend import ModelBackend, ModelError
from amber.runtime.backends.builtins import BuiltinBackend
from amber.runtime.backends.live import CallOutcome, LiveBackend, ToolError
from amber.runtime.guards import PROCEED, GuardEvent, Guards, GuardStop
from amber.runtime.judgment import judge
from amber.runtime.ledger import Ledger
from amber.runtime.program_api import Program, load_program
from amber.trace.canonical import split_canonical
from amber.trace.models import Usage
from amber.util.hashing import digest
from amber.util.logging import get_logger
from amber.util.paths import amber_home
from amber.util.time import utcnow

log = get_logger("runtime.runner")
T = TypeVar("T", bound=BaseModel)
Status = Literal["ok", "stopped", "failed"]


@dataclass
class StepLog:
    idx: int
    tool: str
    status: str  # ok | dry-run | error | llm
    duration_ms: int
    detail: str = ""


@dataclass
class RunResult:
    run_id: str
    workflow: str
    version: int
    status: Status
    mode: str
    steps: int
    llm_calls: int
    llm_input_tokens: int
    llm_output_tokens: int
    cost_usd: float | None
    duration_ms: int
    agent_turns: int = 0
    stop_kind: str | None = None
    stop_detail: str | None = None
    error: str | None = None
    output: dict[str, Any] | None = None
    guard_events: list[GuardEvent] = field(default_factory=list[GuardEvent])
    step_log: list[StepLog] = field(default_factory=list[StepLog])

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow": self.workflow,
            "version": self.version,
            "status": self.status,
            "mode": self.mode,
            "steps": self.steps,
            "llm_calls": self.llm_calls,
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "agent_turns": self.agent_turns,
            "stop_kind": self.stop_kind,
            "stop_detail": self.stop_detail,
            "error": self.error,
            "output": self.output,
            "guard_events": [e.__dict__ for e in self.guard_events],
            "step_log": [s.__dict__ for s in self.step_log],
        }


class LiveContext:
    """Sync ``Context`` over the live/builtin backends, guards, judgment model and ledger."""

    def __init__(
        self,
        *,
        manifest: Manifest,
        guards: Guards,
        live: LiveBackend | None,
        builtins: BuiltinBackend,
        llm_backend: ModelBackend | None,
        model: str,
        ledger: Ledger,
        run_id: str,
        loop: asyncio.AbstractEventLoop,
        on_step: Callable[[StepLog], None] | None = None,
    ) -> None:
        self.manifest = manifest
        self.guards = guards
        self.live = live
        self.builtins = builtins
        self.llm_backend = llm_backend
        self.model = model
        self.ledger = ledger
        self.run_id = run_id
        self.loop = loop
        self.on_step = on_step
        self.cursor = 0
        self.steps = 0
        self.llm_calls = 0
        self.usage = Usage()
        self.step_log: list[StepLog] = []

    # -- plumbing -------------------------------------------------------------
    def _submit(self, coro: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        remaining = self.guards.remaining()
        try:
            return future.result(timeout=None if remaining is None else remaining + 1)
        except TimeoutError:
            future.cancel()
            raise GuardStop("budget", "wall-clock budget exceeded", step_idx=self.cursor) from None

    def _step_for(self, tool: str) -> int:
        for step in self.manifest.steps[self.cursor :]:
            if step.tool == tool:
                self.cursor = step.idx + 1
                return step.idx
        return self.cursor

    def _emit(self, entry: StepLog) -> None:
        self.step_log.append(entry)
        if self.on_step is not None:
            self.on_step(entry)

    # -- Context --------------------------------------------------------------
    def tool(self, name: str, /, **args: Any) -> Any:
        return self._submit(self._tool(name, dict(args)))

    async def _tool(self, name: str, args: dict[str, Any]) -> Any:
        step_idx = self._step_for(name)
        verdict = self.guards.before_call(step_idx, name, args)
        if verdict is not PROCEED:
            self.steps += 1
            self.ledger.add_step(
                self.run_id, step_idx, kind="tool", tool=name, args=args,
                result_digest=digest(verdict), duration_ms=0, status="dry-run",
            )  # fmt: skip
            self._emit(
                StepLog(
                    step_idx, name, "dry-run", 0, "write effect; pass --allow-writes to execute"
                )
            )
            return verdict
        outcome = await self._call_with_retry(step_idx, name, args)
        try:
            self.guards.after_call(step_idx, name, outcome.value, is_error=False)
        except GuardStop as stop:
            self.steps += 1
            self.ledger.add_step(
                self.run_id,
                step_idx,
                kind="tool",
                tool=name,
                args=args,
                result_digest=digest(outcome.value),
                duration_ms=outcome.duration_ms,
                status="stopped",
            )
            self._emit(StepLog(step_idx, name, "stopped", outcome.duration_ms, stop.detail[:120]))
            raise
        self.steps += 1
        self.ledger.add_step(
            self.run_id, step_idx, kind="tool", tool=name, args=args,
            result_digest=digest(outcome.value), duration_ms=outcome.duration_ms, status="ok",
        )  # fmt: skip
        self._emit(StepLog(step_idx, name, "ok", outcome.duration_ms))
        return outcome.value

    async def _call_with_retry(self, step_idx: int, name: str, args: dict[str, Any]) -> CallOutcome:
        server, bare = split_canonical(name)
        for attempt in (0, 1):
            try:
                if server == "builtin":
                    t0 = time.monotonic()
                    value = await self.builtins.call(bare, args, step_idx=step_idx)
                    return CallOutcome(
                        value,
                        "text" if isinstance(value, str) else "json",
                        int((time.monotonic() - t0) * 1000),
                    )
                if self.live is None:
                    raise AmberError(f"no live backend for server {server!r}")
                outcome = await self.live.call(name, args)
                if outcome.is_error:
                    raise ToolError(name, outcome.error_text)
                return outcome
            except ToolError as e:
                if e.transient and attempt == 0:
                    self.guards.record(
                        "error",
                        "soft",
                        f"transient tool error, retrying once: {e.text[:120]}",
                        step_idx,
                    )
                    continue
                self.steps += 1
                self.ledger.add_step(
                    self.run_id, step_idx, kind="tool", tool=name, args=args,
                    result_digest="", duration_ms=0, status="error",
                )  # fmt: skip
                self._emit(StepLog(step_idx, name, "error", 0, e.text[:120]))
                self.guards.after_call(step_idx, name, None, is_error=True, error_text=e.text)
                raise
        raise AssertionError("unreachable")  # pragma: no cover

    def llm(self, step_id: str, schema: type[T], prompt: str, context: dict[str, Any]) -> T:
        return self._submit(self._llm(step_id, schema, prompt, context))

    async def _llm(self, step_id: str, schema: type[T], prompt: str, context: dict[str, Any]) -> T:
        spec = self.manifest.llm_step(step_id)
        step_idx = spec.step_idx if spec is not None else self.cursor
        if self.llm_backend is None:
            raise GuardStop(
                "llm_output", "no model backend configured for judgment steps", step_idx=step_idx
            )
        last = ""
        t0 = time.monotonic()
        for _attempt in (0, 1):
            try:
                value, usage = await judge(
                    self.llm_backend, step_id, schema, prompt, context, model=self.model
                )
            except (ModelError, ValidationError) as e:
                last = str(e).splitlines()[0]
                continue
            self.usage = self.usage + usage
            self.llm_calls += 1
            confidence = getattr(value, "confidence", None)
            self.ledger.add_llm_call(
                self.run_id, step_idx, step_id, self.model,
                input_tokens=usage.total_input, output_tokens=usage.output_tokens, valid=True,
                confidence=float(confidence) if isinstance(confidence, int | float) else None,
            )  # fmt: skip
            self.guards.after_llm(step_idx, value, usage)
            self._emit(
                StepLog(
                    step_idx,
                    f"llm:{step_id}",
                    "llm",
                    int((time.monotonic() - t0) * 1000),
                    f"{usage.total_input}+{usage.output_tokens} tok",
                )
            )
            return value
        self.llm_calls += 1
        self.ledger.add_llm_call(
            self.run_id,
            step_idx,
            step_id,
            self.model,
            input_tokens=0,
            output_tokens=0,
            valid=False,
            confidence=None,
        )
        raise self.guards.llm_invalid(step_idx, last)

    def now(self) -> datetime:
        return utcnow()

    def log(self, msg: str) -> None:
        log.info("%s", msg)


def run_program(
    root: Path,
    cfg: AmberConfig,
    info: VersionInfo,
    raw_inputs: dict[str, Any],
    *,
    allow_writes: bool = False,
    llm_backend: ModelBackend | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
    force: bool = False,
    on_step: Callable[[StepLog], None] | None = None,
) -> RunResult:
    """Execute a program version live; never raises for guard stops (they are in the result)."""
    manifest = info.manifest()
    if manifest is None:
        raise AmberError(f"{info.path} has no manifest.json", hint="Recompile the workflow.")
    if not manifest.usable and not force:
        raise AmberError(
            f"{manifest.workflow} v{manifest.version} is not usable (verify did not pass)",
            hint=f"Run `amber verify {manifest.workflow}` or pass --force.",
        )
    lint_program(info.program_path.read_text(encoding="utf-8"), where=str(info.program_path))
    program: Program = load_program(info.program_path)
    try:
        inputs = program.parse_inputs(raw_inputs)
    except ValidationError as e:
        first = e.errors()[0]
        raise UsageError(
            f"invalid inputs: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}",
            hint="Pass every input with --input name=value; see the Inputs model in program.py.",
        ) from e

    mode = "live" if allow_writes else "dry-run"
    ledger = Ledger(amber_home(root) / "amber.db")
    run_id = ledger.open_run(manifest.workflow, manifest.version, mode, raw_inputs)
    guards = Guards(
        manifest,
        allow_writes=allow_writes,
        min_confidence=cfg.guards.min_confidence,
        wall_clock_s=timeout_s,
    )
    builtins = BuiltinBackend(root, cfg.builtins, bash_templates=manifest.bash_templates)
    needs_live = any(s != "builtin" for s in manifest.servers)
    live = LiveBackend(manifest, cfg.servers, cwd=root) if needs_live else None

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="amber-runner-loop", daemon=True)
    thread.start()
    status: Status = "failed"
    stop_kind: str | None = None
    stop_detail: str | None = None
    error: str | None = None
    output: dict[str, Any] | None = None
    ctx: LiveContext | None = None
    t0 = time.perf_counter()
    try:
        if live is not None:
            events = asyncio.run_coroutine_threadsafe(live.start(), loop).result(timeout=120)
            guards.drift(events)
        ctx = LiveContext(
            manifest=manifest, guards=guards, live=live, builtins=builtins,
            llm_backend=llm_backend, model=model or cfg.models.judgment,
            ledger=ledger, run_id=run_id, loop=loop, on_step=on_step,
        )  # fmt: skip
        result = program.entry(inputs, ctx)
        output = result.model_dump() if isinstance(result, BaseModel) else dict(result or {})
        status = "ok"
    except GuardStop as g:
        status, stop_kind, stop_detail = "stopped", g.kind, g.detail
    except AmberError as e:
        error = f"{type(e).__name__}: {e.message}" + (f" ({e.hint})" if e.hint else "")
    except Exception as e:  # the program itself crashed
        error = f"{type(e).__name__}: {e}"
    finally:
        if live is not None:
            try:
                asyncio.run_coroutine_threadsafe(live.stop(), loop).result(timeout=30)
            except Exception:
                log.debug("live backend did not stop cleanly", exc_info=True)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    for event in guards.events:
        ledger.add_guard_event(run_id, event.step_idx, event.guard, event.severity, event.detail)
    steps = ctx.steps if ctx else 0
    llm_calls = ctx.llm_calls if ctx else 0
    usage = ctx.usage if ctx else Usage()
    ledger.close_run(
        run_id, status=status, steps=steps, llm_calls=llm_calls,
        llm_input_tokens=usage.total_input, llm_output_tokens=usage.output_tokens, cost_usd=None,
        stop_kind=stop_kind, stop_detail=stop_detail or error,
    )  # fmt: skip
    return RunResult(
        run_id=run_id,
        workflow=manifest.workflow,
        version=manifest.version,
        status=status,
        mode=mode,
        steps=steps, llm_calls=llm_calls, llm_input_tokens=usage.total_input,
        llm_output_tokens=usage.output_tokens, cost_usd=None, duration_ms=duration_ms,
        stop_kind=stop_kind, stop_detail=stop_detail, error=error, output=output,
        guard_events=list(guards.events), step_log=list(ctx.step_log) if ctx else [],
    )  # fmt: skip
