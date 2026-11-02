"""Benchmark protocol (``docs/09-launch.md``): agent baseline vs compiled program.

    uv run python bench/run_bench.py --example weekly-report --runs 3           # record + compile
    uv run python bench/run_bench.py --all --runs 3 --baseline existing         # reuse recordings
    uv run python bench/run_bench.py --example weekly-report --runs 3 --offline # no model at all

**Agent baseline.** ``--baseline record`` (default) makes N fresh ``amber record`` runs per example
(needs a logged-in ``claude`` CLI; same flags as the example's ``make record``) and compiles.
``--baseline existing`` takes the successful recordings already in the example's
``.amber/traces`` with ``origin == "record"`` (fallback traces are excluded). Per recording:
``usage.total_input`` (input + cache_read + cache_creation), output tokens, the CLI's
``cost_usd``, ``duration_ms`` and ``num_turns``.

**Compiled program.** One ``amber run --allow-writes`` of the latest usable version per baseline
recording, with the same inputs and, when the recording is tagged ``scenario-X``, the same
scenario (``scenario.py X``). Per run: llm tokens (input incl. cache + output incl. thinking),
the backend-reported cost, wall time, agent turns (must be 0) and the tool-call fidelity:

* ``exact``: the run's tool sequence equals the recording's (builtins excluded);
* ``core``: it equals the recording's calls the program's steps were compiled from
  (``manifest.trace_calls``), i.e. without the exploratory reads that alignment pruned.

``--offline`` is ``--baseline existing`` with the recorded model backend
(``tests/fixtures/model_fills``): no model is called and the compiled llm tokens are that
backend's prompt-length estimate. Results go to ``bench/results.json`` (raw) and
``bench/results.md`` (medians, min/max, models, date, commit, command, mode). Only that table may
be quoted as Amber's own numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
AGENT_MODEL = "claude-sonnet-5-5"
# (scenario, input) per recording and the extra claude flags of each example's `make record`
EXAMPLES: dict[str, dict[str, Any]] = {
    "weekly-report": {
        "runs": [("A", "week=2026-W42"), ("A", "week=2026-W43"), ("A", "week=2026-W44")],
        "key": "week",
        "allow": ["mcp__shop__*", "mcp__slack__*"],
        "agent_flags": [],
    },
    "issue-triage": {
        "runs": [("A", "since=2026-10-27"), ("A", "since=2026-10-29"), ("A", "since=2026-10-30")],
        "key": "since",
        "allow": ["mcp__github__*", "mcp__slack__*"],
        "agent_flags": [],
    },
    "price-watch": {
        "runs": [("A", "product=all"), ("A2", "product=all"), ("A2", "product=lantern-brass")],
        "key": "product",
        "allow": ["mcp__playwright__*", "mcp__alerts__*"],
        "agent_flags": ["--disallowedTools", "Read,Glob,Grep"],
    },
}


def sh(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> Any:
    proc = subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )  # fmt: skip
    if proc.returncode not in (0, 4):
        raise SystemExit(f"{' '.join(args)} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise SystemExit(f"{' '.join(args)} did not return JSON:\n{proc.stdout[-2000:]}") from None


def set_scenario(example: Path, name: str) -> None:
    subprocess.run(
        ["uv", "run", "python", "scenario.py", name], cwd=example, capture_output=True, check=True
    )


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _scenario_of(tags: list[str]) -> str:
    return next((t.removeprefix("scenario-") for t in tags if t.startswith("scenario-")), "A")


def _trace_metrics(trace: dict[str, Any], inputs: str) -> dict[str, Any]:
    usage = trace["outcome"]["usage"]
    calls = [e for e in trace["events"] if e["kind"] == "tool_call"]
    return {
        "trace_id": trace["id"],
        "inputs": inputs,
        "scenario": _scenario_of(trace.get("tags", [])),
        "agent_model": trace.get("harness", {}).get("model"),
        "input_tokens": usage["input_tokens"]
        + usage["cache_read_tokens"]
        + usage["cache_creation_tokens"],
        "output_tokens": usage["output_tokens"],
        "cost_usd": trace["outcome"].get("cost_usd"),
        "duration_ms": trace["outcome"].get("duration_ms"),
        "num_turns": trace["outcome"].get("num_turns"),
        "calls": len(calls),
        "call_sequence": [e["tool"] for e in calls],
        "event_tools": {str(e["idx"]): e["tool"] for e in calls},
    }


def record_baseline(example: Path, name: str, runs: int) -> list[dict[str, Any]]:
    spec = EXAMPLES[name]
    template = (example / "task.md").read_text(encoding="utf-8")
    out: list[dict[str, Any]] = []
    for i in range(runs):
        scenario, value = spec["runs"][i % len(spec["runs"])]
        set_scenario(example, scenario)
        prompt = template.replace("{" + spec["key"] + "}", value.split("=", 1)[1])
        args = ["uv", "run", "amber", "record", name, "--input", value, "--mcp", "mcp.json",
                "--tag", "bench", "--tag", f"scenario-{scenario}"]  # fmt: skip
        for pattern in spec["allow"]:
            args += ["--allow", pattern]
        # explicit --model: never the user's default; --setting-sources user: not this repo's
        # CLAUDE.md
        args += ["--permission-mode", "acceptEdits", "--json", "--", "claude", "-p", prompt]
        args += ["--model", AGENT_MODEL, "--setting-sources", "user", *spec["agent_flags"]]
        out.append(_trace_metrics(sh(args, example), value))
    set_scenario(example, "A")
    return out


def existing_baseline(example: Path, name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sh(["uv", "run", "amber", "traces", "ls", name, "--json"], example):
        trace = sh(["uv", "run", "amber", "traces", "show", row["id"], "--json"], example)
        if trace["outcome"]["status"] == "success" and trace.get("origin", "record") == "record":
            inputs = ",".join(f"{k}={v}" for k, v in trace["inputs"].items())
            out.append(_trace_metrics(trace, inputs))
    return out


def _manifest(example: Path, name: str, version: int) -> dict[str, Any]:
    path = example / "programs" / name / f"v{version}" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def compiled_runs(
    example: Path,
    name: str,
    runs: int,
    baseline: list[dict[str, Any]],
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(runs):
        base = baseline[i % len(baseline)] if baseline else None
        scenario = base["scenario"] if base else "A"
        inputs = base["inputs"] if base else EXAMPLES[name]["runs"][0][1]
        set_scenario(example, scenario)
        args = ["uv", "run", "amber", "run", name, "--allow-writes", "--json"]
        for pair in inputs.split(","):
            args += ["--input", pair]
        result = sh(args, example, env)
        sequence = [s["tool"] for s in result["step_log"] if s["status"] in ("ok", "dry-run")]
        exact = core = None
        if base:
            recorded = [t for t in base["call_sequence"] if not t.startswith("builtin.")]
            exact = 1.0 if sequence == recorded else 0.0
            positions = _manifest(example, name, result["version"]).get("trace_calls", {})
            wanted = positions.get(base["trace_id"])
            if wanted is not None:
                core_seq = [base["event_tools"][str(p)] for p in wanted if p >= 0]
                core = 1.0 if sequence == core_seq else 0.0
        out.append(
            {
                "run_id": result["run_id"],
                "version": result["version"],
                "inputs": inputs,
                "scenario": scenario,
                "status": result["status"],
                "stop": result.get("stop_detail"),
                "llm_calls": result["llm_calls"],
                "llm_input_tokens": result["llm_input_tokens"],
                "llm_output_tokens": result["llm_output_tokens"],
                "cost_usd": result.get("cost_usd"),
                "duration_ms": result["duration_ms"],
                "calls": result["steps"],
                "agent_turns": result["agent_turns"],
                "sequence": sequence,
                "fidelity_exact": exact,
                "fidelity_core": core,
            }
        )
    set_scenario(example, "A")
    return out


def _span(values: list[float]) -> tuple[float, float, float]:
    return median(values), min(values, default=0.0), max(values, default=0.0)


def summarize(
    name: str, baseline: list[dict[str, Any]], compiled: list[dict[str, Any]]
) -> dict[str, Any]:
    agent_tok = [float(b["input_tokens"] + b["output_tokens"]) for b in baseline]
    comp_tok = [float(c["llm_input_tokens"] + c["llm_output_tokens"]) for c in compiled]
    agent_cost = [float(b["cost_usd"]) for b in baseline if b["cost_usd"] is not None]
    comp_cost = [float(c["cost_usd"]) for c in compiled if c["cost_usd"] is not None]
    exact = [c["fidelity_exact"] for c in compiled if c["fidelity_exact"] is not None]
    core = [c["fidelity_core"] for c in compiled if c["fidelity_core"] is not None]
    return {
        "example": name,
        "agent_runs": len(baseline),
        "compiled_runs": len(compiled),
        "agent_tokens": _span(agent_tok),
        "compiled_tokens": _span(comp_tok),
        "ratio": (median(agent_tok) / median(comp_tok)) if median(comp_tok) else None,
        "agent_cost_usd": median(agent_cost) if agent_cost else None,
        "compiled_cost_usd": median(comp_cost) if comp_cost else None,
        "agent_wall_s": median([float(b["duration_ms"] or 0) / 1000 for b in baseline]),
        "compiled_wall_s": median([float(c["duration_ms"]) / 1000 for c in compiled]),
        "llm_calls": max((c["llm_calls"] for c in compiled), default=0),
        "fidelity_exact": sum(exact) / len(exact) if exact else None,
        "fidelity_core": sum(core) / len(core) if core else None,
        "agent_turns": max((c["agent_turns"] for c in compiled), default=0),
        "statuses": sorted({c["status"] for c in compiled}),
        "agent_models": sorted({b["agent_model"] for b in baseline if b["agent_model"]}),
    }


def _git_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or "unknown"


def _claude_version() -> str:
    try:
        proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
    except OSError:
        return "not installed"
    return proc.stdout.strip() or "not run"


def _models(names: list[str]) -> str:
    seen: dict[str, str] = {}
    for name in names:
        cfg = tomllib.loads((REPO / "examples" / name / "amber.toml").read_text(encoding="utf-8"))
        models = cfg.get("models", {})
        seen[f"synthesis={models.get('synthesis')}"] = ""
        seen[f"judgment={models.get('judgment')}"] = ""
    return ", ".join(seen)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.3f}"


def render_md(rows: list[dict[str, Any]], *, mode: str, command: str) -> str:
    names = [r["example"] for r in rows]
    agents = sorted({m for r in rows for m in r["agent_models"]})
    lines = [
        "# Benchmark results",
        "",
        f"- date: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- commit: `{_git_commit()}`",
        f"- command: `{command}`",
        f"- claude CLI: {_claude_version()}",
        f"- agent model (baseline recordings): {', '.join(agents) or 'n/a'}",
        f"- compiled program models (amber.toml): {_models(names)}",
        f"- mode: {mode}",
        "",
        "| example | agent tokens (median, min-max) | compiled llm tokens (median, min-max) | "
        "ratio | agent $ | compiled $ | agent wall s | compiled wall s | llm calls | "
        "fidelity exact | fidelity core | agent turns |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        a, c = r["agent_tokens"], r["compiled_tokens"]
        ratio = f"{r['ratio']:.1f}x" if r["ratio"] else "n/a"
        lines.append(
            f"| {r['example']} | {a[0]:,.0f} ({a[1]:,.0f}-{a[2]:,.0f}) | "
            f"{c[0]:,.0f} ({c[1]:,.0f}-{c[2]:,.0f}) | {ratio} | {_usd(r['agent_cost_usd'])} | "
            f"{_usd(r['compiled_cost_usd'])} | {r['agent_wall_s']:.1f} | "
            f"{r['compiled_wall_s']:.1f} | {r['llm_calls']} | {_pct(r['fidelity_exact'])} | "
            f"{_pct(r['fidelity_core'])} | {r['agent_turns']} |"
        )
    lines += [
        "",
        "Tokens: agent = input incl. cache reads/writes + output per recorded run; compiled = all",
        "`llm()` input incl. cache + output incl. thinking per run. `$` is the API-equivalent",
        "cost the claude CLI reports (a claude.ai subscription is not billed per call).",
        "Fidelity: share of compiled runs whose tool sequence equals the recording for the same",
        "input and scenario; *core* ignores the agent's exploratory reads that alignment pruned.",
        "",
        "Only this table may be quoted as Amber's own numbers (CLAUDE.md #5).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--example", choices=sorted(EXAMPLES), help="one example")
    parser.add_argument("--all", action="store_true", help="all examples")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--baseline", choices=["record", "existing"], default="record")
    parser.add_argument("--offline", action="store_true", help="existing + recorded model backend")
    args = parser.parse_args(argv)
    names = sorted(EXAMPLES) if args.all else ([args.example] if args.example else [])
    if not names:
        parser.error("pass --example NAME or --all")
    baseline_mode = "existing" if args.offline else args.baseline
    env: dict[str, str] | None = None
    if args.offline:
        # never call a model from the offline protocol: replay the fixture fills instead
        env = {
            **os.environ,
            "AMBER_MODEL_BACKEND": "recorded",
            "AMBER_MODEL_FILLS": str(REPO / "tests" / "fixtures" / "model_fills"),
        }
    results: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    for name in names:
        example = REPO / "examples" / name
        if baseline_mode == "existing":
            baseline = existing_baseline(example, name)
        else:
            baseline = record_baseline(example, name, args.runs)
            sh(["uv", "run", "amber", "compile", name, "--json"], example)
        compiled = compiled_runs(example, name, args.runs, baseline, env)
        raw[name] = {"baseline": baseline, "compiled": compiled}
        results.append(summarize(name, baseline, compiled))
        last = results[-1]
        print(
            f"{name}: agent median {last['agent_tokens'][0]:,.0f} tok, compiled median "
            f"{last['compiled_tokens'][0]:,.0f} tok, statuses {last['statuses']}"
        )
    if args.offline:
        mode = (
            "OFFLINE: agent baseline = the recordings already in .amber/traces; compiled runs "
            "against the mock servers with the recorded model backend, so compiled llm tokens "
            "are a prompt-length estimate, not measured usage"
        )
    elif baseline_mode == "existing":
        mode = (
            "LIVE: agent baseline = the live `claude -p` recordings in each example's "
            ".amber/traces (origin record); compiled runs live against the mock servers, "
            "llm() through the claude CLI backend"
        )
    else:
        mode = "LIVE: fresh `claude -p` recordings as the baseline, then compile and run live"
    out_dir = REPO / "bench"
    (out_dir / "results.json").write_text(
        json.dumps({"results": results, "raw": raw}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    command = "uv run python bench/run_bench.py " + " ".join(argv)
    (out_dir / "results.md").write_text(
        render_md(results, mode=mode, command=command), encoding="utf-8", newline="\n"
    )
    print(f"wrote {out_dir / 'results.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
