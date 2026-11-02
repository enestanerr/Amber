"""Doctor checks (``docs/05-runtime.md`` §4). Each returns a :class:`Finding`.

``servers`` and ``schema_drift`` talk to the live servers (through the same ``LiveBackend``
the runner uses); the others read the ledger and the trace store.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from amber.compile.lint import lint_source
from amber.compile.manifest import Manifest
from amber.compile.versions import VersionInfo
from amber.config import AmberConfig
from amber.runtime.backends.live import LiveBackend
from amber.runtime.guards import GuardEvent
from amber.runtime.ledger import Ledger
from amber.trace.store import TraceStore
from amber.util.hashing import sha256_hex

Severity = Literal["ok", "soft", "hard"]


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    detail: str

    def to_json(self) -> dict[str, Any]:
        return {"check": self.check, "severity": self.severity, "detail": self.detail}


# --- live checks ----------------------------------------------------------------


async def _probe(
    manifest: Manifest, cfg: AmberConfig, root: Path
) -> tuple[list[GuardEvent], str | None]:
    live = LiveBackend(manifest, cfg.servers, cwd=root)
    try:
        events = await live.start()
    except Exception as e:  # unreachable / misconfigured server
        return [], f"{type(e).__name__}: {e}"
    finally:
        await live.stop()
    return events, None


def check_servers_and_drift(manifest: Manifest, cfg: AmberConfig, root: Path) -> list[Finding]:
    servers = [s for s in manifest.servers if s != "builtin"]
    if not servers:
        return [
            Finding("servers", "ok", "no MCP servers needed"),
            Finding("schema_drift", "ok", "no MCP tools"),
        ]
    events, error = asyncio.run(_probe(manifest, cfg, root))
    if error is not None:
        return [
            Finding(
                "servers",
                "hard",
                f"could not connect to all of {', '.join(servers)}: {error[:200]}",
            ),
            Finding("schema_drift", "ok", "not checked (servers unreachable)"),
        ]
    findings = [Finding("servers", "ok", f"{len(servers)}/{len(servers)} reachable")]
    hard = [e for e in events if e.severity == "hard"]
    soft = [e for e in events if e.severity == "soft"]
    if hard:
        findings.append(Finding("schema_drift", "hard", "; ".join(e.detail for e in hard[:3])))
    elif soft:
        findings.append(Finding("schema_drift", "soft", "; ".join(e.detail for e in soft[:3])))
    else:
        findings.append(Finding("schema_drift", "ok", f"{len(manifest.tools)} tool schemas match"))
    return findings


# --- ledger checks --------------------------------------------------------------


def check_result_shape(
    ledger: Ledger, workflow: str, runs: int, *, version: int | None = None
) -> Finding:
    recent = ledger.recent_runs(workflow, runs, version=version)
    if not recent:
        return Finding("result_shape", "ok", "no runs yet")
    hard_runs = 0
    soft_runs = 0
    hard_detail = soft_detail = ""
    for run in recent:
        events = [e for e in ledger.guard_events(run.id) if e["guard"] == "result_shape"]
        if any(e["severity"] == "hard" for e in events):
            hard_runs += 1
            hard_detail = hard_detail or next(
                e["detail"] for e in events if e["severity"] == "hard"
            )
        elif any(e["severity"] == "soft" for e in events):
            soft_runs += 1
            soft_detail = soft_detail or next(
                e["detail"] for e in events if e["severity"] == "soft"
            )
    if hard_runs:
        return Finding(
            "result_shape",
            "hard",
            f"{hard_detail} in {hard_runs}/{len(recent)} recent runs (hard)",
        )
    if soft_runs * 2 >= len(recent):
        return Finding(
            "result_shape",
            "soft",
            f"{soft_detail} in {soft_runs}/{len(recent)} recent runs (soft)",
        )
    return Finding("result_shape", "ok", f"no shape drift in the last {len(recent)} runs")


def check_guard_stats(
    ledger: Ledger, workflow: str, runs: int, *, version: int | None = None
) -> Finding:
    recent = ledger.recent_runs(workflow, runs, version=version)
    if not recent:
        return Finding("guard_stats", "ok", "no runs yet")
    stopped = sum(1 for r in recent if r.status == "stopped")
    rate = stopped / len(recent)
    detail = f"stop rate {stopped}/{len(recent)} ({rate:.0%})"
    if rate >= 0.6:
        return Finding("guard_stats", "hard", detail)
    if rate >= 0.3:
        return Finding("guard_stats", "soft", detail)
    return Finding("guard_stats", "ok", detail)


def check_llm_health(
    ledger: Ledger,
    workflow: str,
    runs: int,
    min_confidence: float,
    *,
    version: int | None = None,
) -> Finding:
    recent = ledger.recent_runs(workflow, runs, version=version)
    calls = [c for r in recent for c in ledger.llm_calls(r.id)]
    if not calls:
        return Finding("llm_health", "ok", "no llm calls yet")
    invalid = sum(1 for c in calls if not c["valid"])
    low = sum(1 for c in calls if c["confidence"] is not None and c["confidence"] < min_confidence)
    if invalid / len(calls) >= 0.2:
        return Finding(
            "llm_health", "soft", f"{invalid}/{len(calls)} llm outputs failed validation"
        )
    if low / len(calls) >= 0.5:
        return Finding(
            "llm_health",
            "soft",
            f"{low}/{len(calls)} llm outputs below confidence {min_confidence}",
        )
    return Finding(
        "llm_health",
        "ok",
        f"{invalid} validation failures, {low} low-confidence in {len(calls)} calls",
    )


def check_staleness(store: TraceStore, manifest: Manifest) -> Finding:
    newer = [
        s
        for s in store.list(manifest.workflow)
        if s.id not in manifest.source_traces
        and s.status != "failure"
        and s.created_at > manifest.created_at
    ]
    if newer:
        ids = ", ".join(s.id for s in newer[:3])
        return Finding(
            "staleness", "soft", f"{len(newer)} trace(s) newer than v{manifest.version}: {ids}"
        )
    return Finding("staleness", "ok", f"v{manifest.version} covers all traces")


def check_program_lint(info: VersionInfo, manifest: Manifest) -> Finding:
    source = info.program_path.read_text(encoding="utf-8")
    problems = lint_source(source)
    if problems:
        return Finding("program_lint", "hard", "; ".join(problems[:3]))
    actual = sha256_hex(source)[:16]
    if manifest.program_hash and actual != manifest.program_hash:
        return Finding(
            "program_lint",
            "hard",
            f"program.py was edited after compile "
            f"(hash {manifest.program_hash[:6]} → {actual[:6]})",
        )
    return Finding("program_lint", "ok", "program.py passes lint and matches the manifest")
