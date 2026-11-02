"""Pydantic models shared by the HTML pages and their ``/api`` JSON twins."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverviewRow(_Model):
    workflow: str
    latest_version: int | None
    usable: bool
    runs_7d: int
    stop_rate: float
    agent_median_tokens: int
    compiled_llm_tokens: int
    tokens_saved: int
    traces: int


class ProgramRow(_Model):
    workflow: str
    version: int
    usable: bool
    created_at: str
    source_traces: list[str] = Field(default_factory=list[str])
    steps: int
    judgment_steps: int
    transform_steps: int
    path: str


class RunSummary(_Model):
    id: str
    workflow: str
    version: int
    started_at: str
    finished_at: str | None
    status: str
    mode: str
    inputs: dict[str, Any] = Field(default_factory=dict[str, Any])
    steps: int
    llm_calls: int
    llm_input_tokens: int
    llm_output_tokens: int
    cost_usd: float | None
    agent_turns: int
    stop_kind: str | None
    stop_detail: str | None
    duration_s: float | None


class RunDetail(_Model):
    run: RunSummary
    steps: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    guard_events: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    llm_calls: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    decisions: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])


class DecisionRow(_Model):
    id: int
    run_id: str
    workflow: str
    ts: str
    decision: str
    reason: str
    actor: str
    vetoes: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    proposal: dict[str, Any] = Field(default_factory=dict[str, Any])
