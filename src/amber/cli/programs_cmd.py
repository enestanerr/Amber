"""``amber programs ls [workflow]`` · ``show <wf> [--version N]`` · ``diff <wf> <v1> <v2>``."""

from __future__ import annotations

import difflib
from typing import Annotated, Any

import typer
from rich.syntax import Syntax
from rich.table import Table

from amber.cli._context import project
from amber.cli._output import JsonOpt, get_output
from amber.compile.manifest import Manifest
from amber.compile.versions import get_version, list_versions, list_workflows

programs_app = typer.Typer(help="Inspect compiled program versions.", no_args_is_help=True)


@programs_app.command("ls")
def ls(
    ctx: typer.Context,
    workflow: Annotated[str | None, typer.Argument(help="Filter by workflow.")] = None,
    json_: JsonOpt = False,
) -> None:
    """List program versions (usable, traces, llm steps, transforms, created)."""
    out = get_output(ctx, json_)
    root, _ = project(ctx)
    names = [workflow] if workflow else list_workflows(root)
    rows: list[dict[str, Any]] = []
    for name in names:
        for v in list_versions(root, name):
            m = v.manifest()
            rows.append(
                {
                    "workflow": name,
                    "version": v.version,
                    "usable": bool(m and m.usable),
                    "traces": len(m.source_traces) if m else 0,
                    "llm_steps": len(m.llm_steps) if m else 0,
                    "transforms": len(m.transforms) if m else 0,
                    "created_at": m.created_at.isoformat(timespec="seconds") if m else "",
                    "path": str(v.path),
                }
            )
    if out.json_mode:
        out.json(rows)
        return
    table = Table(title=f"Programs ({len(rows)})")
    for col in ("workflow", "version", "usable", "traces", "llm steps", "transforms", "created"):
        table.add_column(
            col,
            justify="right" if col in ("version", "traces", "llm steps", "transforms") else "left",
        )
    for r in rows:
        table.add_row(
            r["workflow"], f"v{r['version']}", "yes" if r["usable"] else "no", str(r["traces"]),
            str(r["llm_steps"]), str(r["transforms"]), r["created_at"][:16],
        )  # fmt: skip
    out.print(table)
    if not rows:
        out.print("No programs yet. Run `amber compile <workflow>`.")


@programs_app.command("show")
def show(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument()],
    version: Annotated[
        int | None, typer.Option("--version", "-v", help="Version (default latest).")
    ] = None,
    manifest_only: Annotated[
        bool, typer.Option("--manifest", help="Print the manifest instead of the program.")
    ] = False,
    json_: JsonOpt = False,
) -> None:
    """Print program.py (or the manifest) of a version."""
    out = get_output(ctx, json_)
    root, _ = project(ctx)
    info = get_version(root, workflow, version)
    manifest = info.manifest()
    source = info.program_path.read_text(encoding="utf-8")
    if out.json_mode:
        out.json(
            {
                "workflow": workflow,
                "version": info.version,
                "path": str(info.path),
                "manifest": manifest.model_dump(mode="json") if manifest else None,
                "program": source,
            }
        )
        return
    if manifest_only and manifest is not None:
        out.json(manifest)
        return
    out.print(Syntax(source, "python", line_numbers=True, word_wrap=False))


def manifest_delta(a: Manifest | None, b: Manifest | None) -> list[str]:
    lines: list[str] = []
    if a is None or b is None:
        return ["(manifest missing on one side)"]
    tools_a, tools_b = {t.canonical for t in a.tools}, {t.canonical for t in b.tools}
    for t in sorted(tools_b - tools_a):
        lines.append(f"+ tool {t}")
    for t in sorted(tools_a - tools_b):
        lines.append(f"- tool {t}")
    llm_a, llm_b = {s.id for s in a.llm_steps}, {s.id for s in b.llm_steps}
    for s in sorted(llm_b - llm_a):
        lines.append(f"+ llm step {s}")
    for s in sorted(llm_a - llm_b):
        lines.append(f"- llm step {s}")
    in_a = set(a.inputs_schema.get("properties", {}))
    in_b = set(b.inputs_schema.get("properties", {}))
    for n in sorted(in_b - in_a):
        lines.append(f"+ input {n}")
    for n in sorted(in_a - in_b):
        lines.append(f"- input {n}")
    if a.usable != b.usable:
        lines.append(f"usable {a.usable} → {b.usable}")
    return lines or ["(no manifest differences in tools, llm steps or inputs)"]


@programs_app.command("diff")
def diff(
    ctx: typer.Context,
    workflow: Annotated[str, typer.Argument()],
    v1: Annotated[int, typer.Argument()],
    v2: Annotated[int, typer.Argument()],
    json_: JsonOpt = False,
) -> None:
    """Unified diff of program.py plus a manifest delta between two versions."""
    out = get_output(ctx, json_)
    root, _ = project(ctx)
    a, b = get_version(root, workflow, v1), get_version(root, workflow, v2)
    src_a = a.program_path.read_text(encoding="utf-8").splitlines(keepends=True)
    src_b = b.program_path.read_text(encoding="utf-8").splitlines(keepends=True)
    udiff = "".join(difflib.unified_diff(src_a, src_b, f"v{v1}/program.py", f"v{v2}/program.py"))
    delta = manifest_delta(a.manifest(), b.manifest())
    if out.json_mode:
        out.json({"workflow": workflow, "v1": v1, "v2": v2, "diff": udiff, "manifest_delta": delta})
        return
    out.print(Syntax(udiff or "(programs are identical)", "diff"))
    out.print("\n".join(delta))
