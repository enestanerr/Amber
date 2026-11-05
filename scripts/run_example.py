#!/usr/bin/env python3
"""Run Amber example targets without ``make``.

This is the Windows-friendly replacement for the example ``Makefile``\\ s:
it translates each target into the same ``amber`` / ``scenario.py``
invocations, but as a plain Python program, so it needs no ``make``, no
Git Bash, no ``sed`` and no shell loops. It works from PowerShell, ``cmd``
and POSIX shells alike because it never uses ``shell=True``: every command
is a list of arguments, paths come from :mod:`pathlib`, and the task-text
substitution that the Makefiles did with ``sed`` is a :meth:`str.replace`.

Prerequisites (unchanged from the Makefiles): Python 3.12+ and ``uv``.
``uv`` is still required because ``uv run amber`` manages the project
environment and because the examples' ``amber.toml`` / ``mcp.json`` spawn
their mock servers with ``uv run python ...``. What this script removes is
only the ``make`` dependency, not the ``uv`` one. Install ``uv`` first,
for example with ``winget install astral-sh.uv`` on Windows, then run
``uv sync --all-extras`` once from the repository root.

Usage, from the repository root::

    python scripts/run_example.py weekly-report demo-offline
    python scripts/run_example.py price-watch drift
    python scripts/run_example.py --dry-run issue-triage demo

``--dry-run`` prints every command (with its working directory and any
extra environment variables) without running anything, which is also how
the target translations are checked on platforms where the live tools
are unavailable. ``record`` and ``demo`` need a logged-in ``claude`` CLI
and are refused with a clear error when it is not on ``PATH``.

The root ``Makefile`` shortcuts are accepted as single arguments:
``demo`` and ``demo-offline`` mean the weekly-report targets, while
``drift`` and ``heal`` mean the price-watch targets.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Root-Makefile shortcuts: single argument -> (example, target).
ROOT_ALIASES = {
    "demo": ("weekly-report", "demo"),
    "demo-offline": ("weekly-report", "demo-offline"),
    "drift": ("price-watch", "drift"),
    "heal": ("price-watch", "heal"),
}


@dataclass(frozen=True)
class RecordSpec:
    """One ``amber record`` invocation's varying inputs."""

    inputs: list[str]
    task_placeholder: str
    tag: str | None = None
    scenario: str | None = None


@dataclass
class Example:
    """Everything the Makefiles knew about one example."""

    workflow: str
    allows: list[str]
    record_specs: list[RecordSpec] = field(default_factory=list)
    run_args: list[str] = field(default_factory=list)
    heal_run_args: list[str] = field(default_factory=list)
    run_scenario: str | None = None
    supports_seed: bool = False
    supports_serve: bool = False
    extra_clean: list[str] = field(default_factory=list)


EXAMPLES: dict[str, Example] = {
    "weekly-report": Example(
        workflow="weekly-report",
        allows=["mcp__shop__*", "mcp__slack__*"],
        record_specs=[
            RecordSpec(inputs=[f"week={week}"], task_placeholder="{week}")
            for week in ("2026-W42", "2026-W43", "2026-W44")
        ],
        run_args=["--input", "week=2026-W40", "--allow-writes"],
        heal_run_args=["--input", "week=2026-W41", "--allow-writes", "--fallback", "agent"],
        supports_seed=True,
    ),
    "issue-triage": Example(
        workflow="issue-triage",
        allows=["mcp__github__*", "mcp__slack__*"],
        record_specs=[
            RecordSpec(inputs=[f"since={day}"], task_placeholder="{since}")
            for day in ("2026-10-27", "2026-10-29", "2026-10-30")
        ],
        run_args=["--input", "since=2026-10-28", "--allow-writes"],
        heal_run_args=["--input", "since=2026-10-28", "--allow-writes", "--fallback", "agent"],
    ),
    "price-watch": Example(
        workflow="price-watch",
        allows=["mcp__playwright__*", "mcp__alerts__*"],
        record_specs=[
            RecordSpec(
                inputs=[f"product={product}"],
                task_placeholder="{product}",
                tag=f"scenario-{scenario}",
                scenario=scenario,
            )
            for scenario, product in (
                ("A", "all"),
                ("A2", "all"),
                ("A2", "lantern-brass"),
            )
        ],
        run_args=["--input", "product=torch-iron", "--allow-writes"],
        heal_run_args=["--input", "product=all", "--allow-writes", "--fallback", "agent"],
        run_scenario="A2",
        supports_serve=True,
        extra_clean=[".playwright-mcp"],
    ),
}

#: Targets every example supports, in Makefile order.
COMMON_TARGETS = (
    "record",
    "align",
    "compile",
    "verify",
    "run",
    "drift",
    "undrift",
    "heal",
    "demo",
    "clean",
)
RECORDED_ENV = {"AMBER_MODEL_BACKEND": "recorded"}


@dataclass
class Step:
    """One command to run, with its working directory and env additions."""

    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    note: str = ""


_AMBER_WARNING_PRINTED = False


def amber_command() -> list[str]:
    """How to invoke ``amber``: ``uv run amber`` when uv exists.

    Falls back to a bare ``amber`` on PATH, then to ``python -m amber``.
    The last fallback only works if Amber was installed some other way;
    the caller prints a hint in that case (once, not once per step).
    """
    global _AMBER_WARNING_PRINTED
    if shutil.which("uv") is not None:
        return ["uv", "run", "amber"]
    if shutil.which("amber") is not None:
        return ["amber"]
    if not _AMBER_WARNING_PRINTED:
        _AMBER_WARNING_PRINTED = True
        print(
            "warning: neither 'uv' nor 'amber' is on PATH; trying 'python -m amber'. "
            "Run 'uv sync --all-extras' from the repository root if this fails.",
            file=sys.stderr,
        )
    return [sys.executable, "-m", "amber"]


def python_command() -> list[str]:
    """Plain interpreter for ``scenario.py`` and the static file server.

    The scenario scripts add ``examples/_shared`` to ``sys.path``
    themselves, so they do not need ``uv run``.
    """
    return [sys.executable]


def _amber(example_dir: Path, *args: str, env: dict[str, str] | None = None) -> Step:
    return Step(argv=[*amber_command(), *args], cwd=example_dir, env=env or {})


def _scenario(example_dir: Path, *args: str) -> Step:
    return Step(argv=[*python_command(), "scenario.py", *args], cwd=example_dir)


def _record_steps(
    name: str,
    example: Example,
    example_dir: Path,
    model: str,
    dry_run: bool = False,
) -> list[Step]:
    claude = shutil.which("claude")
    if claude is None and not dry_run:
        raise SystemExit(
            "error: 'record' needs a logged-in 'claude' CLI on PATH "
            "(the Makefile 'record' target has the same requirement)."
        )
    # In dry-run mode there is nothing to execute, so show the literal
    # 'claude' command instead of refusing to print the translation.
    claude_cmd = claude if claude is not None else "claude"
    task = (example_dir / "task.md").read_text(encoding="utf-8")
    steps: list[Step] = []
    for spec in example.record_specs:
        if spec.scenario is not None:
            steps.append(_scenario(example_dir, spec.scenario))
        argv = [*amber_command(), "record", example.workflow]
        for value in spec.inputs:
            argv += ["--input", value]
        if spec.tag is not None:
            argv += ["--tag", spec.tag]
        argv += ["--mcp", "mcp.json"]
        for allow in example.allows:
            argv += ["--allow", allow]
        argv += [
            "--permission-mode",
            "acceptEdits",
            "--",
            claude_cmd,
            "-p",
            task.replace(spec.task_placeholder, spec.inputs[0].split("=", 1)[1]),
            "--model",
            model,
            "--setting-sources",
            "user",
        ]
        if name == "price-watch":
            argv += ["--disallowedTools", "Read,Glob,Grep"]
        steps.append(Step(argv=argv, cwd=example_dir))
    if name == "price-watch":
        # The Makefile restores scenario A after the recording loop.
        steps.append(_scenario(example_dir, "A"))
    return steps


def _seed_steps(example_dir: Path, example: Example) -> list[Step]:
    """Copy the synthetic fixture traces, as the Makefile ``seed`` does."""
    traces = REPO / "tests" / "fixtures" / "traces" / example.workflow
    dest = example_dir / ".amber" / "traces" / example.workflow
    steps: list[Step] = []
    for trace in ("t1.json", "t2.json", "t3.json"):
        steps.append(
            Step(
                argv=[
                    sys.executable,
                    "-c",
                    "import shutil, sys; from pathlib import Path; "
                    "Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True); "
                    "shutil.copyfile(sys.argv[1], sys.argv[2])",
                    str(traces / trace),
                    str(dest / trace),
                ],
                cwd=example_dir,
                note=f"seed {trace} (copies a fixture; no shell 'cp' needed)",
            )
        )
    return steps


def build_steps(name: str, target: str, model: str, dry_run: bool = False) -> list[Step]:
    """Translate one example target into steps, without running anything."""
    example = EXAMPLES[name]
    example_dir = REPO / "examples" / name
    workflow = example.workflow
    fills = str(REPO / "tests" / "fixtures" / "model_fills")
    recorded_env = {**RECORDED_ENV, "AMBER_MODEL_FILLS": fills}

    if target == "record":
        return _record_steps(name, example, example_dir, model, dry_run)
    if target == "align":
        return [_amber(example_dir, "align", workflow, "--show")]
    if target == "compile":
        return [_amber(example_dir, "compile", workflow)]
    if target == "verify":
        return [_amber(example_dir, "verify", workflow)]
    if target == "run":
        steps = []
        if example.run_scenario is not None:
            steps.append(_scenario(example_dir, example.run_scenario))
        steps.append(_amber(example_dir, "run", workflow, *example.run_args))
        return steps
    if target == "drift":
        return [_scenario(example_dir, "B")]
    if target == "undrift":
        return [_scenario(example_dir, "A")]
    if target == "heal":
        return [
            _amber(example_dir, "run", workflow, *example.heal_run_args),
            _amber(example_dir, "compile", workflow, "--refresh"),
            _amber(example_dir, "verify", workflow),
        ]
    if target == "site":
        if not example.supports_serve:
            raise SystemExit(f"error: '{name}' has no 'site' target (only price-watch does).")
        return [_scenario(example_dir)]
    if target == "serve":
        if not example.supports_serve:
            raise SystemExit(f"error: '{name}' has no 'serve' target (only price-watch does).")
        return [
            Step(
                argv=[*python_command(), "-m", "http.server", "8765", "--directory", "site"],
                cwd=example_dir,
                note="foreground server: stop with Ctrl+C, as with 'make serve'",
            )
        ]
    if target == "seed":
        if not example.supports_seed:
            raise SystemExit(f"error: '{name}' has no 'seed' target (only weekly-report does).")
        return _seed_steps(example_dir, example)
    if target == "demo-offline":
        if not example.supports_seed:
            raise SystemExit(
                f"error: '{name}' has no 'demo-offline' target (only weekly-report does)."
            )
        return [
            *_seed_steps(example_dir, example),
            _amber(example_dir, "compile", workflow, env=recorded_env),
            _amber(example_dir, "verify", workflow),
            _amber(
                example_dir,
                "run",
                workflow,
                "--input",
                "week=2026-W44",
                "--dry-run",
                env=recorded_env,
            ),
        ]
    if target == "demo":
        return [
            *_record_steps(name, example, example_dir, model, dry_run),
            _amber(example_dir, "align", workflow, "--show"),
            _amber(example_dir, "compile", workflow),
            _amber(example_dir, "verify", workflow),
            *build_steps(name, "run", model, dry_run),
        ]
    if target == "clean":
        return [
            Step(
                argv=[
                    sys.executable,
                    "-c",
                    "import shutil, sys; "
                    "[shutil.rmtree(p, ignore_errors=True) for p in sys.argv[1:]]",
                    *[
                        str(example_dir / d)
                        for d in (
                            ".amber",
                            ".outbox",
                            "programs",
                            ".scenario",
                            *example.extra_clean,
                        )
                    ],
                ],
                cwd=example_dir,
                note="removes runtime output only; never touches the example sources",
            )
        ]
    raise SystemExit(f"error: unknown target '{target}' for '{name}'.")


def available_targets(name: str) -> list[str]:
    """Targets the named example supports."""
    targets = list(COMMON_TARGETS)
    if EXAMPLES[name].supports_seed:
        targets += ["seed", "demo-offline"]
    if EXAMPLES[name].supports_serve:
        targets += ["site", "serve"]
    return targets


def describe(step: Step) -> str:
    """One human-readable line for a step (display only; runs shell-free)."""
    env = " ".join(f"{key}={value}" for key, value in step.env.items())
    line = f"+ (in {step.cwd.relative_to(REPO)}) {shlex.join(step.argv)}"
    if env:
        line += f"  [env {env}]"
    if step.note:
        line += f"  # {step.note}"
    return line


def run_steps(steps: list[Step], dry_run: bool) -> int:
    """Print, then run (unless dry-run), each step; stop at the first failure."""
    for step in steps:
        # Flush so the "+ ..." line stays ahead of the step's own output
        # when stdout is piped (subprocess output is not buffered).
        print(describe(step), flush=True)
        if dry_run:
            continue
        env = {**os.environ, **step.env}
        result = subprocess.run(step.argv, cwd=step.cwd, env=env, check=False)
        if result.returncode != 0:
            print(
                f"failed with exit code {result.returncode}: {shlex.join(step.argv)}",
                file=sys.stderr,
            )
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run an Amber example target without make (see the module docstring).",
    )
    parser.add_argument(
        "example",
        nargs="?",
        help="weekly-report, issue-triage, price-watch, "
        "or a root alias (demo, demo-offline, drift, heal)",
    )
    parser.add_argument("target", nargs="?", help="example target; omit it when using a root alias")
    parser.add_argument(
        "--model",
        default="claude-sonnet-5-5",
        help="agent model for 'record' (default: claude-sonnet-5-5, as in the Makefiles)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the commands without running them"
    )
    parser.add_argument(
        "--list", action="store_true", help="list examples and their targets, then exit"
    )
    args = parser.parse_args(argv)

    if args.list:
        for name in EXAMPLES:
            print(f"{name}: {' '.join(available_targets(name))}")
        print(f"root aliases: {' '.join(sorted(ROOT_ALIASES))}")
        return 0

    if args.example in ROOT_ALIASES and args.target is None:
        name, target = ROOT_ALIASES[args.example]
    elif args.example in EXAMPLES and args.target is not None:
        name, target = args.example, args.target
    else:
        parser.error(
            "give '<example> <target>' (for example 'weekly-report demo-offline') "
            "or a single root alias (demo, demo-offline, drift, heal)."
        )
        return 2

    if target not in available_targets(name):
        available = " ".join(available_targets(name))
        print(f"error: '{name}' has no target '{target}'. Available: {available}", file=sys.stderr)
        return 2
    try:
        steps = build_steps(name, target, args.model, args.dry_run)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    return run_steps(steps, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
