"""``amber compile`` orchestration: traces → skeleton → holes → synthesis → codegen → lint →
``programs/<workflow>/v<N>/``. Verify (P5) marks the version usable afterwards."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from amber.align.models import Skeleton
from amber.align.skeleton import build_skeleton
from amber.compile.codegen import Fills, generate
from amber.compile.holes import Hole, collect_holes
from amber.compile.lint import lint_program
from amber.compile.manifest import Manifest, build_manifest
from amber.compile.report import render_report
from amber.compile.synthesizer import HoleOutcome, TransformFill, synthesize
from amber.compile.versions import VersionInfo, next_version, save_manifest, write_version
from amber.config import AmberConfig
from amber.errors import AmberError
from amber.models.backend import ModelBackend
from amber.runtime.backends.builtins import SUPPORTED as SUPPORTED_BUILTINS
from amber.runtime.program_api import load_program
from amber.runtime.verify import VerifyReport, mark_usable, verify_version
from amber.trace.canonical import canonicalize_args
from amber.trace.jsonpath import MISSING, get_path
from amber.trace.models import ToolCall, Trace
from amber.util.hashing import sha256_hex
from amber.util.time import utcnow


@dataclass
class CompileResult:
    workflow: str
    version: int
    path: Path
    usable: bool
    skeleton: Skeleton
    manifest: Manifest
    holes: list[Hole]
    outcomes: list[HoleOutcome]
    program_source: str
    warnings: list[str] = field(default_factory=list[str])
    dry_run: bool = False
    verify: VerifyReport | None = None

    @property
    def holes_filled(self) -> int:
        return sum(1 for o in self.outcomes if o.accepted)

    def to_json(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "version": self.version,
            "path": str(self.path),
            "usable": self.usable,
            "dry_run": self.dry_run,
            "skeleton_hash": self.skeleton.hash,
            "program_hash": self.manifest.program_hash,
            "holes_filled": self.holes_filled,
            "verify": self.verify.to_json() if self.verify is not None else None,
            "holes": [
                {
                    "id": o.hole.id,
                    "kind": o.hole.kind,
                    "step": o.hole.step_idx,
                    "attempts": o.attempts,
                    "accepted": o.accepted,
                    "downgraded": o.downgraded,
                    "notes": o.notes,
                }
                for o in self.outcomes
            ],
            "warnings": self.warnings,
        }


def select_traces(traces: list[Trace], *, include_failed: bool, warnings: list[str]) -> list[Trace]:
    kept: list[Trace] = []
    for t in traces:
        if t.outcome.status == "failure" and not include_failed:
            warnings.append(f"skipping failed trace {t.id} (use --include-failed)")
            continue
        kept.append(t)
    return kept


def fills_from_outcomes(outcomes: list[HoleOutcome]) -> Fills:
    fills = Fills()
    for o in outcomes:
        if not o.accepted or o.fill is None:
            continue
        if isinstance(o.fill, TransformFill):
            fills.transforms[o.hole.id] = o.fill.python_source
        else:
            fills.judgments[o.hole.id] = (o.fill.prompt, o.fill.fields)
    return fills


async def compile_workflow(
    root: Path,
    cfg: AmberConfig,
    traces: list[Trace],
    *,
    backend: ModelBackend | None,
    model: str | None = None,
    refresh: bool = False,
    max_repair: int = 3,
    dry_run: bool = False,
    keep_unreferenced: bool = False,
) -> CompileResult:
    if not traces:
        raise AmberError("no traces to compile", hint="Record runs first with `amber record`.")
    warnings: list[str] = []
    skeleton = build_skeleton(traces, keep_unreferenced=keep_unreferenced)
    warnings.extend(skeleton.warnings)
    check_builtins(skeleton)
    holes = collect_holes(skeleton, traces)
    outcomes: list[HoleOutcome] = []
    if not dry_run and holes:
        if backend is None:
            raise AmberError(
                "no model backend for hole synthesis", hint="Use --dry-run to inspect the skeleton."
            )
        outcomes = await synthesize(
            holes,
            skeleton,
            backend,
            model=model or cfg.models.synthesis,
            max_repair=max_repair,
            warnings=warnings,
        )
        # downgrades changed step kinds → recollect so codegen sees judgment holes
        holes = collect_holes(skeleton, traces)
        skeleton.hash = skeleton.compute_hash()
    version = next_version(root, skeleton.workflow, skeleton.hash, refresh=refresh)
    generated = generate(skeleton, traces, holes, fills_from_outcomes(outcomes), version=version)
    lint_program(generated.source)
    created_at = utcnow()
    program_hash = sha256_hex(generated.source)[:16]
    manifest = build_manifest(
        skeleton,
        traces,
        version=version,
        created_at=created_at,
        budgets=cfg.budgets,
        inputs_schema={},
        output_schema={},
        steps=generated.steps,
        llm_steps=generated.llm_steps,
        transforms=generated.transforms,
        warnings=warnings,
    )
    manifest.program_hash = program_hash
    for entry in manifest.transforms:
        for o in outcomes:
            if o.hole.id == entry.id and o.accepted:
                entry.verified_on = [ex.trace_id for ex in o.hole.examples]
            if o.hole.step_idx == entry.step_idx and o.downgraded:
                entry.downgraded = True
    report = render_report(
        skeleton,
        version=version,
        created_at=created_at,
        program_hash=program_hash,
        usable=False,
        outcomes=outcomes,
        warnings=warnings,
    )
    info: VersionInfo = write_version(
        root,
        manifest,
        program_source=generated.source,
        test_source=generated.test_source,
        report=report,
    )
    # The program must import cleanly; its models give the manifest its schemas.
    program = load_program(info.program_path)
    manifest.inputs_schema = program.inputs_model.model_json_schema()
    manifest.output_schema = program.output_model.model_json_schema()
    manifest.trace_calls = trace_calls(skeleton)
    manifest.trace_inputs = trace_inputs(skeleton, traces)
    manifest.placeholder_results = placeholder_results(skeleton, traces)
    manifest.bash_templates = bash_templates(skeleton)
    save_manifest(info, manifest)
    verify_report: VerifyReport | None = None
    usable = False
    if not dry_run:
        verify_report = verify_version(info, traces)
        usable = mark_usable(info, verify_report)
        manifest = info.manifest() or manifest
    return CompileResult(
        workflow=skeleton.workflow,
        version=version,
        path=info.path,
        usable=usable,
        skeleton=skeleton,
        manifest=manifest,
        holes=holes,
        outcomes=outcomes,
        program_source=generated.source,
        warnings=warnings,
        dry_run=dry_run,
        verify=verify_report,
    )


class UnsupportedBuiltin(AmberError):
    exit_code = 1


def check_builtins(skeleton: Skeleton) -> None:
    """Compile-time error for builtins the runtime cannot execute (``docs/05-runtime.md`` §2.2)."""
    for step in skeleton.steps:
        server, _, name = step.tool.partition(".")
        if server == "builtin" and name not in SUPPORTED_BUILTINS:
            raise UnsupportedBuiltin(
                f"step {step.idx + 1} uses builtin.{name}, which Amber cannot run",
                hint="Re-record with MCP tools or mark the step as judgment.",
            )


def placeholder_results(skeleton: Skeleton, traces: list[Trace]) -> dict[int, Any]:
    """Recorded result (first trace, volatile paths masked) for every non-read step."""
    by_id = {t.id: t for t in traces}
    out: dict[int, Any] = {}
    for step in skeleton.steps:
        if step.effect == "read":
            continue
        tid = next(iter(step.calls))
        call = by_id[tid].events[step.calls[tid]]
        assert isinstance(call, ToolCall)
        result: Any = call.result
        if isinstance(result, dict):
            masked = canonicalize_args(
                cast(dict[str, Any], result), skeleton.volatile_paths.get(step.idx, [])
            )
            result = masked
        out[step.idx] = result
    return out


def bash_templates(skeleton: Skeleton) -> dict[int, str]:
    """Regex per ``builtin.Bash`` step: recorded literals fixed, provenance values wildcarded."""
    out: dict[int, str] = {}
    for step in skeleton.steps:
        if step.tool != "builtin.Bash":
            continue
        prov = step.args.get("$.command")
        if prov is None or prov.kind == "unknown":
            raise UnsupportedBuiltin(
                f"step {step.idx + 1} runs a Bash command Amber cannot reconstruct "
                "from the recordings",
                hint="Compiled programs may only run recorded commands; re-record with MCP tools "
                "or mark the step as judgment.",
            )
        if prov.kind == "const":
            out[step.idx] = re.escape(str(prov.value))
        elif prov.kind == "template" and prov.parts:
            out[step.idx] = "".join(
                re.escape(p.literal) if p.literal is not None else "(.*?)" for p in prov.parts
            )
        else:
            out[step.idx] = "(.*?)"
    return out


def trace_calls(skeleton: Skeleton) -> dict[str, list[int]]:
    """``trace id → [event idx per kept step, -1 if absent]`` for the cassette."""
    return {tid: [step.calls.get(tid, -1) for step in skeleton.steps] for tid in skeleton.traces}


def trace_inputs(skeleton: Skeleton, traces: list[Trace]) -> dict[str, dict[str, Any]]:
    """Declared inputs plus inferred ones read back from each trace's recorded args."""
    by_id = {t.id: t for t in traces}
    out: dict[str, dict[str, Any]] = {}
    for tid in skeleton.traces:
        values: dict[str, Any] = dict(by_id[tid].inputs)
        for name, spec in skeleton.inputs.items():
            if spec.source != "inferred" or name in values:
                continue
            for step in skeleton.steps:
                if tid not in step.calls:
                    continue
                for path, prov in step.args.items():
                    if prov.kind == "input" and prov.name == name:
                        call = by_id[tid].events[step.calls[tid]]
                        assert isinstance(call, ToolCall)
                        leaf = get_path(call.args, path)
                        if leaf is not MISSING:
                            values[name] = leaf
                        break
                if name in values:
                    break
        out[tid] = values
    return out
