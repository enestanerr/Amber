"""Benchmark protocol (``docs/09-launch.md``): agent baseline vs compiled program.

    uv run python bench/run_bench.py --example weekly-report --runs 3
    uv run python bench/run_bench.py --all --runs 3

For each example: N fresh ``amber record`` runs (tagged ``bench``; needs a logged-in ``claude``
CLI) collect ``usage.total_input``, ``usage.output_tokens``, ``cost_usd``, ``duration_ms``,
``num_turns`` and the call count; then N ``amber run --allow-writes`` runs of the latest usable
version collect llm tokens, duration, calls, ``agent_turns`` (must be 0) and the tool-call
fidelity (sequence of canonical names equal to the recording for the same input). Results go
to ``bench/results.json`` (raw) and ``bench/results.md`` (medians, min/max, model ids, date,
commit, command). Only that table may be quoted as Amber's numbers.

``--offline`` skips the live recordings and uses the recordings already in the example's
``.amber/traces`` (e.g. the synthetic fixtures seeded by ``make seed``) as the baseline; the
compiled runs execute against the mock servers with the *recorded* model backend
(``tests/fixtures/model_fills``), so no model is called and the compiled llm token figures are the
recorded backend's prompt-length estimate, not measured usage. The markdown header says so.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
EXAMPLES: dict[str, dict[str, Any]] = {
    "weekly-report": {
        "inputs": ["week=2026-W42", "week=2026-W43", "week=2026-W44"],
        "key": "week",
    },
    "issue-triage": {
        "inputs": ["since=2026-10-27", "since=2026-10-28", "since=2026-10-29"],
        "key": "since",
    },
    "price-watch": {
        "inputs": ["product=all", "product=lantern-brass", "product=all"],
        "key": "product",
    },
}


def sh(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> Any:
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 4):
        raise SystemExit(f"{' '.join(args)} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise SystemExit(f"{' '.join(args)} did not return JSON:\n{proc.stdout[-2000:]}") from None


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _trace_metrics(trace: dict[str, Any], inputs: str) -> dict[str, Any]:
    usage = trace["outcome"]["usage"]
    calls = [e["tool"] for e in trace["events"] if e["kind"] == "tool_call"]
    return {
        "trace_id": trace["id"],
        "inputs": inputs,
        "input_tokens": usage["input_tokens"]
        + usage["cache_read_tokens"]
        + usage["cache_creation_tokens"],
        "output_tokens": usage["output_tokens"],
        "cost_usd": trace["outcome"].get("cost_usd"),
        "duration_ms": trace["outcome"].get("duration_ms"),
        "num_turns": trace["outcome"].get("num_turns"),
        "calls": len(calls),
        "call_sequence": calls,
    }


def record_baseline(example: Path, name: str, runs: int, agent_model: str) -> list[dict[str, Any]]:
    spec = EXAMPLES[name]
    template = (example / "task.md").read_text(encoding="utf-8")
    out: list[dict[str, Any]] = []
    for i in range(runs):
        value = spec["inputs"][i % len(spec["inputs"])]
        prompt = template.replace("{" + spec["key"] + "}", value.split("=", 1)[1])
        # explicit --model: never fall back to the user's default (possibly expensive) model;
        # --setting-sources user: the agent must not inherit this repository's CLAUDE.md
        args = [
            "uv", "run", "amber", "record", name, "--input", value, "--mcp", "mcp.json",
            "--tag", "bench", "--allow", "mcp__*", "--permission-mode", "acceptEdits",
            "--json", "--", "claude", "-p", prompt, "--model", agent_model,
            "--setting-sources", "user",
        ]  # fmt: skip
        out.append(_trace_metrics(sh(args, example), value))
    return out


def existing_baseline(example: Path, name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in sh(["uv", "run", "amber", "traces", "ls", name, "--json"], example):
        trace = sh(["uv", "run", "amber", "traces", "show", row["id"], "--json"], example)
        if trace["outcome"]["status"] == "success":
            inputs = ",".join(f"{k}={v}" for k, v in trace["inputs"].items())
            out.append(_trace_metrics(trace, inputs))
    return out


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
        inputs = base["inputs"] if base else EXAMPLES[name]["inputs"][0]
        args = ["uv", "run", "amber", "run", name, "--allow-writes", "--json"]
        for pair in inputs.split(","):
            args += ["--input", pair]
        result = sh(args, example, env)
        sequence = [s["tool"] for s in result["step_log"] if s["status"] in ("ok", "dry-run")]
        recorded = [
            t for t in (base or {}).get("call_sequence", []) if not t.startswith("builtin.")
        ]
        out.append(
            {
                "run_id": result["run_id"],
                "inputs": inputs,
                "status": result["status"],
                "llm_input_tokens": result["llm_input_tokens"],
                "llm_output_tokens": result["llm_output_tokens"],
                "duration_ms": result["duration_ms"],
                "calls": result["steps"],
                "agent_turns": result["agent_turns"],
                "fidelity": 1.0 if recorded and sequence == recorded else 0.0,
            }
        )
    return out


def summarize(
    name: str, baseline: list[dict[str, Any]], compiled: list[dict[str, Any]]
) -> dict[str, Any]:
    agent_in = [float(b["input_tokens"]) for b in baseline]
    comp_tok = [float(c["llm_input_tokens"] + c["llm_output_tokens"]) for c in compiled]
    agent_wall = [float(b["duration_ms"] or 0) / 1000 for b in baseline]
    comp_wall = [float(c["duration_ms"]) / 1000 for c in compiled]
    return {
        "example": name,
        "agent_runs": len(baseline),
        "compiled_runs": len(compiled),
        "agent_input_tokens_median": median(agent_in),
        "agent_input_tokens_min": min(agent_in, default=0),
        "agent_input_tokens_max": max(agent_in, default=0),
        "compiled_llm_tokens_median": median(comp_tok),
        "compiled_llm_tokens_min": min(comp_tok, default=0),
        "compiled_llm_tokens_max": max(comp_tok, default=0),
        "ratio": (median(agent_in) / median(comp_tok)) if median(comp_tok) else None,
        "agent_wall_s_median": median(agent_wall),
        "compiled_wall_s_median": median(comp_wall),
        "fidelity": median([c["fidelity"] for c in compiled]) if compiled else None,
        "agent_turns": max((c["agent_turns"] for c in compiled), default=0),
        "statuses": sorted({c["status"] for c in compiled}),
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
    proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
    return proc.stdout.strip() or "not run"


def render_md(rows: list[dict[str, Any]], *, offline: bool, command: str, agent_model: str) -> str:
    toml = (REPO / "examples" / "weekly-report" / "amber.toml").read_text(encoding="utf-8")
    models = [
        line.split("#", 1)[0].strip()
        for line in toml.splitlines()
        if line.startswith(("synthesis", "judgment"))
    ]
    mode = (
        "OFFLINE: agent baseline taken from the recordings already in .amber/traces "
        "(synthetic fixtures unless you recorded live); compiled runs executed against the "
        "mock servers with the recorded model backend, so compiled llm tokens are a "
        "prompt-length estimate, not measured usage"
        if offline
        else "LIVE: fresh claude -p recordings as the baseline"
    )
    lines = [
        "# Benchmark results",
        "",
        f"- date: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- commit: `{_git_commit()}`",
        f"- command: `{command}`",
        f"- claude CLI: {_claude_version()}",
        f"- models (amber.toml): {', '.join(models)}",
        f"- agent model (baseline recordings): {agent_model}",
        f"- mode: {mode}",
        "",
        "| example | agent input tok (median) | compiled llm tok (median) | ratio | "
        "agent wall s | compiled wall s | fidelity | agent turns |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ratio = f"{r['ratio']:.1f}x" if r["ratio"] else "n/a"
        fid = f"{r['fidelity']:.0%}" if r["fidelity"] is not None else "n/a"
        agent = (
            f"{r['agent_input_tokens_median']:,.0f} "
            f"({r['agent_input_tokens_min']:,.0f}-{r['agent_input_tokens_max']:,.0f})"
        )
        comp = (
            f"{r['compiled_llm_tokens_median']:,.0f} "
            f"({r['compiled_llm_tokens_min']:,.0f}-{r['compiled_llm_tokens_max']:,.0f})"
        )
        lines.append(
            f"| {r['example']} | {agent} | {comp} | {ratio} | {r['agent_wall_s_median']:.1f} | "
            f"{r['compiled_wall_s_median']:.1f} | {fid} | {r['agent_turns']} |"
        )
    lines += ["", "Only this table may be quoted as Amber's own numbers (CLAUDE.md #5)."]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--example", choices=sorted(EXAMPLES), help="one example")
    parser.add_argument("--all", action="store_true", help="all examples")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--offline", action="store_true", help="existing recordings as baseline")
    parser.add_argument(
        "--agent-model",
        default="claude-sonnet-5-5",
        help="model for the baseline `claude -p` recordings (default: claude-sonnet-5-5)",
    )
    args = parser.parse_args(argv)
    names = sorted(EXAMPLES) if args.all else ([args.example] if args.example else [])
    if not names:
        parser.error("pass --example NAME or --all")
    results: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    env: dict[str, str] | None = None
    if args.offline:
        # never call a model from the offline protocol: replay the fixture fills instead
        env = {
            **os.environ,
            "AMBER_MODEL_BACKEND": "recorded",
            "AMBER_MODEL_FILLS": str(REPO / "tests" / "fixtures" / "model_fills"),
        }
    for name in names:
        example = REPO / "examples" / name
        if args.offline:
            baseline = existing_baseline(example, name)
        else:
            baseline = record_baseline(example, name, args.runs, args.agent_model)
            sh(["uv", "run", "amber", "compile", name, "--json"], example)
        compiled = compiled_runs(example, name, args.runs, baseline, env)
        raw[name] = {"baseline": baseline, "compiled": compiled}
        results.append(summarize(name, baseline, compiled))
        last = results[-1]
        print(
            f"{name}: agent median {last['agent_input_tokens_median']:,.0f} tok, "
            f"compiled median {last['compiled_llm_tokens_median']:,.0f} tok"
        )
    out_dir = REPO / "bench"
    (out_dir / "results.json").write_text(
        json.dumps({"results": results, "raw": raw}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    command = "uv run python bench/run_bench.py " + " ".join(argv)
    (out_dir / "results.md").write_text(
        render_md(results, offline=args.offline, command=command, agent_model=args.agent_model),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {out_dir / 'results.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
