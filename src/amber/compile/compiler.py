"""``amber compile`` orchestration: traces → skeleton → holes → synthesis → codegen → lint →
``programs/<workflow>/v<N>/``. Verify (P5) marks the version usable afterwards."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from amber.runtime.program_api import load_program
from amber.trace.models import Trace
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
    save_manifest(info, manifest)
    return CompileResult(
        workflow=skeleton.workflow,
        version=version,
        path=info.path,
        usable=False,
        skeleton=skeleton,
        manifest=manifest,
        holes=holes,
        outcomes=outcomes,
        program_source=generated.source,
        warnings=warnings,
        dry_run=dry_run,
    )
