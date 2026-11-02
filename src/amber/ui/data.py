"""Read-mostly data access for the panel: traces, programs, runs, doctor, decisions."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from amber.compile.versions import VersionInfo, get_version, list_versions, list_workflows
from amber.config import AmberConfig, load_config
from amber.doctor.diagnosis import Diagnosis, diagnose
from amber.runtime.ledger import Ledger, RunRow
from amber.trace.store import TraceStore
from amber.ui.charts import GuardRow, TokenRow
from amber.ui.models import DecisionRow, OverviewRow, ProgramRow, RunDetail, RunSummary
from amber.util.json import loads
from amber.util.paths import amber_home
from amber.util.time import utcnow


def _run_summary(r: RunRow) -> RunSummary:
    duration = None
    if r.finished_at:
        try:
            duration = (
                datetime.fromisoformat(r.finished_at) - datetime.fromisoformat(r.started_at)
            ).total_seconds()
        except ValueError:
            duration = None
    return RunSummary(
        id=r.id, workflow=r.workflow, version=r.version, started_at=r.started_at,
        finished_at=r.finished_at, status=r.status, mode=r.mode, inputs=r.inputs, steps=r.steps,
        llm_calls=r.llm_calls, llm_input_tokens=r.llm_input_tokens,
        llm_output_tokens=r.llm_output_tokens, cost_usd=r.cost_usd, agent_turns=r.agent_turns,
        stop_kind=r.stop_kind, stop_detail=r.stop_detail,
        duration_s=duration,
    )  # fmt: skip


@dataclass
class Panel:
    root: Path
    cfg: AmberConfig | None = None
    diagnoses: dict[str, Diagnosis] = field(default_factory=dict[str, Diagnosis])

    def __post_init__(self) -> None:
        self.home = amber_home(self.root)
        self.store = TraceStore(self.home)
        self.ledger = Ledger(self.home / "amber.db")
        if self.cfg is None:
            try:
                self.cfg = load_config(self.root)
            except Exception:
                self.cfg = AmberConfig()

    # -- overview -------------------------------------------------------------
    def workflows(self) -> list[str]:
        names = set(list_workflows(self.root)) | {s.workflow for s in self.store.list()}
        return sorted(names)

    def agent_median_tokens(self, workflow: str, source_traces: list[str] | None = None) -> int:
        summaries = self.store.list(workflow)
        if source_traces:
            summaries = [s for s in summaries if s.id in source_traces] or summaries
        values = [s.input_tokens for s in summaries if s.status != "failure"]
        return int(statistics.median(values)) if values else 0

    def overview(self) -> list[OverviewRow]:
        rows: list[OverviewRow] = []
        since = (utcnow() - timedelta(days=7)).isoformat()
        for wf in self.workflows():
            versions = list_versions(self.root, wf)
            latest = versions[-1] if versions else None
            manifest = latest.manifest() if latest else None
            runs = [r for r in self.ledger.recent_runs(wf, 500) if r.started_at >= since]
            stopped = sum(1 for r in runs if r.status == "stopped")
            compiled = sum(r.llm_input_tokens + r.llm_output_tokens for r in runs)
            median = self.agent_median_tokens(wf, manifest.source_traces if manifest else None)
            rows.append(
                OverviewRow(
                    workflow=wf,
                    latest_version=latest.version if latest else None,
                    usable=bool(manifest and manifest.usable),
                    runs_7d=len(runs),
                    stop_rate=(stopped / len(runs)) if runs else 0.0,
                    agent_median_tokens=median,
                    compiled_llm_tokens=compiled,
                    tokens_saved=max(0, median * len(runs) - compiled),
                    traces=len(self.store.list(wf)),
                )
            )
        return rows

    # -- programs -------------------------------------------------------------
    def programs(self, workflow: str | None = None) -> list[ProgramRow]:
        out: list[ProgramRow] = []
        for wf in [workflow] if workflow else list_workflows(self.root):
            for v in list_versions(self.root, wf):
                m = v.manifest()
                if m is None:
                    continue
                out.append(
                    ProgramRow(
                        workflow=wf,
                        version=v.version,
                        usable=m.usable,
                        created_at=m.created_at.isoformat(timespec="seconds"),
                        source_traces=list(m.source_traces),
                        steps=len(m.steps),
                        judgment_steps=sum(1 for s in m.llm_steps if s.kind == "judgment"),
                        transform_steps=len(m.transforms),
                        path=str(v.path),
                    )
                )
        return out

    def version(
        self, workflow: str, version: int | None = None
    ) -> tuple[VersionInfo, dict[str, Any]]:
        info = get_version(self.root, workflow, version)
        manifest = info.manifest()
        report = info.path / "REPORT.md"
        source = info.program_path.read_text(encoding="utf-8")
        return info, {
            "manifest": manifest.model_dump(mode="json") if manifest else None,
            "report": report.read_text(encoding="utf-8") if report.is_file() else "",
            "source": source,
        }

    # -- runs -----------------------------------------------------------------
    def runs(self, workflow: str | None = None, n: int = 100) -> list[RunSummary]:
        return [_run_summary(r) for r in self.ledger.recent_runs(workflow, n)]

    def run(self, run_id: str) -> RunDetail | None:
        row = self.ledger.run(run_id)
        if row is None:
            return None
        steps = self.ledger.steps(run_id)
        for s in steps:
            s["args"] = loads(s["args"]) if isinstance(s.get("args"), str) else s.get("args")
        return RunDetail(
            run=_run_summary(row),
            steps=steps,
            guard_events=self.ledger.guard_events(run_id),
            llm_calls=self.ledger.llm_calls(run_id),
            decisions=[
                d for d in self.ledger.decisions(row.workflow, 200) if d["run_id"] == run_id
            ],
        )

    def tokens_chart_rows(
        self, workflow: str | None = None, n: int = 12
    ) -> tuple[list[TokenRow], int]:
        runs = list(reversed(self.ledger.recent_runs(workflow, n)))
        rows: list[TokenRow] = []
        medians: dict[str, int] = {}
        for r in runs:
            medians.setdefault(r.workflow, self.agent_median_tokens(r.workflow))
            rows.append(
                TokenRow(r.id[-6:], medians[r.workflow], r.llm_input_tokens + r.llm_output_tokens)
            )
        baseline = int(statistics.median(medians.values())) if medians else 0
        return rows, baseline

    def guard_chart_rows(self, workflow: str | None = None) -> list[GuardRow]:
        events = (
            self.ledger.guard_events(workflow=workflow, n=1000)
            if workflow
            else self.ledger.guard_events(n=1000)
        )
        buckets: dict[str, list[int]] = {}
        for e in events:
            day = str(e["ts"])[:10]
            buckets.setdefault(day, [0, 0])
            if e["severity"] == "hard":
                buckets[day][1] += 1
            elif e["severity"] == "soft":
                buckets[day][0] += 1
        return [GuardRow(day, soft, hard) for day, (soft, hard) in sorted(buckets.items())][-14:]

    # -- doctor / decisions / traces -----------------------------------------
    def run_doctor(self, workflow: str, *, probe: bool = True) -> Diagnosis:
        info = get_version(self.root, workflow)
        assert self.cfg is not None
        d = diagnose(
            self.root, self.cfg, info, store=self.store, ledger=self.ledger, probe_servers=probe
        )
        self.diagnoses[workflow] = d
        return d

    def decisions(self, workflow: str | None = None) -> list[DecisionRow]:
        out: list[DecisionRow] = []
        for d in self.ledger.decisions(workflow, 200):
            out.append(
                DecisionRow(
                    id=d["id"],
                    run_id=d["run_id"],
                    workflow=d["workflow"],
                    ts=d["ts"],
                    decision=d["decision"],
                    reason=d["reason"],
                    actor=d["actor"],
                    vetoes=loads(d["vetoes"] or "[]"),
                    proposal=loads(d["proposal"] or "{}"),
                )
            )
        return out

    def trace(self, trace_id: str) -> dict[str, Any]:
        return self.store.load(trace_id).model_dump(mode="json")
