"""Verify: replay a program version against its source traces (``docs/04-compiler.md`` §E).

``replay`` runs one trace through the :class:`CassetteBackend`; ``verify_version`` runs every
trace and marks the version usable when all pass; ``replay_from_paths`` is the entry point used
by the generated ``tests/test_replay.py``.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from rich.table import Table

from amber.compile.lint import lint_program
from amber.compile.manifest import Manifest
from amber.compile.versions import VersionInfo, save_manifest
from amber.errors import AmberError
from amber.models.backend import ModelBackend
from amber.runtime.backends.cassette import CassetteBackend, ReplayDivergence
from amber.runtime.judgment import Agreement, compare_outputs, judge
from amber.runtime.program_api import Program, load_program
from amber.trace.models import ToolCall, Trace
from amber.trace.store import TraceStore
from amber.util.json import read_json
from amber.util.paths import amber_home, find_project_root


@dataclass
class ReplayReport:
    trace_id: str
    status: Literal["pass", "fail"]
    consumed: int
    expected: int
    leftover: int
    llm_steps: int
    duration_ms: int
    divergence: ReplayDivergence | None = None
    error: str | None = None
    output_match: bool | None = None
    agreements: list[Agreement] = field(default_factory=list[Agreement])

    def summary(self) -> str:
        if self.status == "pass":
            return f"{self.trace_id}: pass ({self.consumed}/{self.expected} calls)"
        if self.divergence is not None:
            return f"{self.trace_id}: {self.divergence.message} — {self.divergence.hint}"
        return f"{self.trace_id}: {self.error}"

    def to_json(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "status": self.status,
            "consumed": self.consumed,
            "expected": self.expected,
            "leftover": self.leftover,
            "llm_steps": self.llm_steps,
            "duration_ms": self.duration_ms,
            "divergence": None
            if self.divergence is None
            else {
                "step_idx": self.divergence.step_idx,
                "expected": self.divergence.expected,
                "actual": self.divergence.actual,
                "diff": self.divergence.diff,
                "hint": self.divergence.hint,
            },
            "error": self.error,
            "output_match": self.output_match,
            "agreements": [a.__dict__ for a in self.agreements],
        }


@dataclass
class VerifyReport:
    workflow: str
    version: int
    reports: list[ReplayReport]

    @property
    def passed(self) -> bool:
        return bool(self.reports) and all(r.status == "pass" for r in self.reports)

    def table(self) -> Table:
        table = Table(title=f"verify {self.workflow} v{self.version}")
        for col in ("trace", "calls", "llm steps", "result", "divergence"):
            table.add_column(col, justify="right" if col in ("calls", "llm steps") else "left")
        for r in self.reports:
            detail = ""
            if r.divergence is not None:
                detail = r.divergence.message + "\n" + "\n".join(r.divergence.diff.splitlines()[:3])
            elif r.error:
                detail = r.error
            calls = f"{r.consumed}/{r.expected}" + (f" (+{r.leftover} left)" if r.leftover else "")
            status = "[green]PASS[/]" if r.status == "pass" else "[red]FAIL[/]"
            if r.agreements:
                agreed = sum(1 for a in r.agreements if a.agree)
                status += f" · llm agree {agreed}/{len(r.agreements)}"
            table.add_row(r.trace_id, calls, str(r.llm_steps), status, detail)
        table.caption = (
            f"{sum(1 for r in self.reports if r.status == 'pass')}/{len(self.reports)} PASS"
        )
        return table

    def rows(self) -> list[dict[str, Any]]:
        return [
            {
                "trace": r.trace_id,
                "calls": f"{r.consumed}/{r.expected}",
                "llm": r.llm_steps,
                "status": r.status.upper(),
                "divergence": (r.divergence.message if r.divergence else (r.error or "")),
            }
            for r in self.reports
        ]

    def to_json(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "version": self.version,
            "passed": self.passed,
            "traces": [r.to_json() for r in self.reports],
        }


def trace_inputs_for(manifest: Manifest, trace: Trace) -> dict[str, Any]:
    return dict(manifest.trace_inputs.get(trace.id, trace.inputs))


def _final_call(trace: Trace, manifest: Manifest) -> ToolCall | None:
    calls = trace.calls
    wanted = manifest.trace_calls.get(trace.id)
    if wanted:
        for event_idx in reversed(wanted):
            if event_idx >= 0:
                event = trace.events[event_idx]
                return event if isinstance(event, ToolCall) else None
    return calls[-1] if calls else None


def _output_match(output: BaseModel, trace: Trace, manifest: Manifest) -> bool | None:
    final = _final_call(trace, manifest)
    if final is None or not isinstance(final.result, dict):
        return None
    recorded: dict[str, Any] = final.result
    produced = output.model_dump()
    comparable = [k for k in produced if k in recorded]
    if not comparable:
        return None
    return all(produced[k] == recorded[k] for k in comparable)


def replay(
    program: Program,
    manifest: Manifest,
    trace: Trace,
    *,
    strict_order: bool = True,
    live_llm: tuple[ModelBackend, str] | None = None,
) -> ReplayReport:
    """Replay one trace through *program*; never raises for divergences (they are reported)."""
    agreements: list[Agreement] = []

    def on_llm(
        step_id: str,
        schema: type[BaseModel],
        prompt: str,
        context: dict[str, Any],
        recorded: BaseModel,
    ) -> None:
        if live_llm is None:
            return
        backend, model = live_llm
        produced, _ = asyncio.run(judge(backend, step_id, schema, prompt, context, model=model))
        found, _ = asyncio.run(
            compare_outputs(
                backend, step_id, recorded.model_dump(), produced.model_dump(), model=model
            )
        )
        agreements.extend(found)

    ctx = CassetteBackend(trace, manifest, strict_order=strict_order, on_llm=on_llm)
    t0 = time.perf_counter()
    divergence: ReplayDivergence | None = None
    error: str | None = None
    output: BaseModel | None = None
    try:
        inputs = program.parse_inputs(trace_inputs_for(manifest, trace))
        output = program.entry(inputs, ctx)
    except ReplayDivergence as d:
        divergence = d
    except AmberError as e:
        error = f"{type(e).__name__}: {e.message}"
    except Exception as e:  # the program itself crashed
        error = f"{type(e).__name__}: {e}"
    duration_ms = int((time.perf_counter() - t0) * 1000)
    leftover = ctx.leftover
    if divergence is None and error is None and leftover:
        head = leftover[0]
        divergence = ReplayDivergence(
            ctx.pos, (head.tool, head.args), None, hint="program skipped a recorded call"
        )
    status: Literal["pass", "fail"] = "pass" if divergence is None and error is None else "fail"
    output_match = (
        _output_match(output, trace, manifest) if status == "pass" and output is not None else None
    )
    return ReplayReport(
        trace_id=trace.id,
        status=status,
        consumed=ctx.consumed,
        expected=len(ctx.expected),
        leftover=len(leftover),
        llm_steps=len(ctx.llm_calls),
        duration_ms=duration_ms,
        divergence=divergence,
        error=error,
        output_match=output_match,
        agreements=agreements,
    )


def verify_version(
    info: VersionInfo,
    traces: list[Trace],
    *,
    strict_order: bool = True,
    live_llm: tuple[ModelBackend, str] | None = None,
) -> VerifyReport:
    manifest = info.manifest()
    if manifest is None:
        raise AmberError(f"{info.path} has no manifest.json", hint="Recompile the workflow.")
    lint_program(info.program_path.read_text(encoding="utf-8"), where=str(info.program_path))
    program = load_program(info.program_path)
    reports = [
        replay(program, manifest, t, strict_order=strict_order, live_llm=live_llm) for t in traces
    ]
    return VerifyReport(manifest.workflow, manifest.version, reports)


def mark_usable(info: VersionInfo, report: VerifyReport) -> bool:
    """Set ``manifest.usable`` from a full-source-set verify and refresh REPORT.md."""
    manifest = info.manifest()
    if manifest is None:
        return False
    verified = {r.trace_id for r in report.reports}
    manifest.usable = report.passed and set(manifest.source_traces) <= verified
    save_manifest(info, manifest)
    update_report_verify(info.path / "REPORT.md", report, manifest.usable)
    return manifest.usable


_VERIFY_SECTION = re.compile(r"## Verify\n.*?(?=\n## |\Z)", re.DOTALL)
_USABLE_LINE = re.compile(r"- usable: \*\*(yes|no)\*\*[^\n]*")


def update_report_verify(path: Path, report: VerifyReport, usable: bool) -> None:
    if not path.is_file():
        return
    rows = report.rows()
    table = ["| trace | calls | llm steps | result | divergence |", "|---|---|---|---|---|"]
    table += [
        f"| {r['trace']} | {r['calls']} | {r['llm']} | {r['status']} | "
        f"{str(r['divergence']).replace('|', '/')} |"
        for r in rows
    ]
    section = "## Verify\n\n" + "\n".join(table) + "\n"
    text = path.read_text(encoding="utf-8")
    text = _VERIFY_SECTION.sub(lambda _: section, text, count=1)
    text = _USABLE_LINE.sub(f"- usable: **{'yes' if usable else 'no'}**", text, count=1)
    path.write_text(text, encoding="utf-8", newline="\n")


def replay_from_paths(test_file: str, trace_id: str) -> ReplayReport:
    """Used by the generated ``tests/test_replay.py``: locate the version dir and the project."""
    version_dir = Path(test_file).resolve().parents[1]
    manifest = Manifest.model_validate(read_json(version_dir / "manifest.json"))
    program = load_program(version_dir / "program.py")
    root = find_project_root(start=version_dir)
    if root is None:
        raise AmberError(
            f"no amber.toml above {version_dir}", hint="Run the replay tests from the project."
        )
    trace = TraceStore(amber_home(root)).load(trace_id)
    return replay(program, manifest, trace)
