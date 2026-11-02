"""Diagnosis: run every check and apply the decision table (``docs/05-runtime.md`` §4).

| findings | status | recommendation |
|---|---|---|
| all ok | healthy | none |
| only ``staleness`` | healthy | recompile (``--refresh``) |
| any soft, no hard | degraded | recompile if ``schema_drift``/``result_shape`` soft, else none |
| hard drift (``schema_drift``/``result_shape``/``guard_stats``), servers up | degraded | fallback |
| ``servers`` hard | broken | re-record |
| ``program_lint`` hard | broken | recompile |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from amber.compile.versions import VersionInfo
from amber.config import AmberConfig
from amber.doctor.checks import (
    Finding,
    check_guard_stats,
    check_llm_health,
    check_program_lint,
    check_result_shape,
    check_servers_and_drift,
    check_staleness,
)
from amber.errors import AmberError
from amber.runtime.ledger import Ledger
from amber.trace.store import TraceStore

Status = Literal["healthy", "degraded", "broken"]
Recommendation = Literal["none", "recompile", "fallback", "re-record"]


@dataclass
class Diagnosis:
    workflow: str
    version: int
    status: Status
    recommendation: Recommendation
    rationale: str
    findings: list[Finding] = field(default_factory=list[Finding])

    def to_json(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "version": self.version,
            "status": self.status,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
            "findings": [f.to_json() for f in self.findings],
        }

    def next_command(self) -> str:
        wf = self.workflow
        return {
            "none": "",
            "recompile": f"run `amber compile {wf} --refresh`",
            "fallback": f"run `amber run {wf} --fallback agent`, "
            f"then `amber compile {wf} --refresh`",
            "re-record": f"fix the server configuration, then `amber record {wf} ...` "
            f"and `amber compile {wf} --refresh`",
        }[self.recommendation]


def decide(findings: list[Finding]) -> tuple[Status, Recommendation, str]:
    by = {f.check: f for f in findings}
    hard = {f.check for f in findings if f.severity == "hard"}
    soft = {f.check for f in findings if f.severity == "soft"}
    if "servers" in hard:
        return "broken", "re-record", "a server is unreachable; a fallback would fail the same way"
    if "program_lint" in hard:
        return "broken", "recompile", "program.py no longer matches what was compiled"
    if hard & {"schema_drift", "result_shape", "guard_stats"}:
        what = ", ".join(sorted(hard & {"schema_drift", "result_shape", "guard_stats"}))
        return (
            "degraded",
            "fallback",
            f"hard drift ({what}) with servers reachable: "
            "the agent should re-derive the changed step",
        )
    if not hard and not soft:
        return "healthy", "none", "all checks passed"
    if soft == {"staleness"} and not hard:
        return "healthy", "recompile", by["staleness"].detail
    if soft & {"schema_drift", "result_shape"}:
        return "degraded", "recompile", "soft drift in tool schemas or result shapes"
    return "degraded", "none", "soft findings only: " + ", ".join(sorted(soft))


def diagnose(
    root: Path,
    cfg: AmberConfig,
    info: VersionInfo,
    *,
    store: TraceStore,
    ledger: Ledger,
    runs: int = 10,
    probe_servers: bool = True,
) -> Diagnosis:
    manifest = info.manifest()
    if manifest is None:
        raise AmberError(f"{info.path} has no manifest.json", hint="Recompile the workflow.")
    findings: list[Finding] = []
    if probe_servers:
        findings.extend(check_servers_and_drift(manifest, cfg, root))
    findings.append(check_result_shape(ledger, manifest.workflow, runs))
    findings.append(check_guard_stats(ledger, manifest.workflow, runs))
    findings.append(check_llm_health(ledger, manifest.workflow, runs, cfg.guards.min_confidence))
    findings.append(check_staleness(store, manifest))
    findings.append(check_program_lint(info, manifest))
    status, recommendation, rationale = decide(findings)
    return Diagnosis(
        manifest.workflow, manifest.version, status, recommendation, rationale, findings
    )
