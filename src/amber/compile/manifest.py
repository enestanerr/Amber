"""``manifest.json`` (``PLAN.md`` P4): everything the runtime, verify, doctor and the panel
need to know about a program version without importing it."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from amber.align.models import Skeleton, StepSpec
from amber.config import BudgetsCfg
from amber.trace.models import ToolCall, Trace


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


FieldType = Literal["str", "int", "float", "bool", "list[str]"]


class FieldSpec(_Model):
    """One field of a judgment schema (also the synthesizer's output shape)."""

    name: str
    type: FieldType = "str"
    description: str = ""
    enum: list[str] | None = None


class ToolEntry(_Model):
    canonical: str
    server: str
    name: str
    effect: str = "unknown"
    schema_hash: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict[str, Any])


class StepSummary(_Model):
    idx: int
    tool: str
    kind: str
    conditional: bool = False
    effect: str = "unknown"
    var: str = ""
    observe: bool = False


class LlmStep(_Model):
    id: str
    step_idx: int
    kind: Literal["judgment", "gate"] = "judgment"
    schema_name: str
    fields: list[FieldSpec] = Field(default_factory=list[FieldSpec])
    context_keys: list[str] = Field(default_factory=list[str])
    field_paths: dict[str, str] = Field(default_factory=dict[str, str])  # field → arg path
    model_hint: str | None = None


class TransformEntry(_Model):
    id: str
    step_idx: int
    function: str
    produces: list[str] = Field(default_factory=list[str])
    verified_on: list[str] = Field(default_factory=list[str])
    downgraded: bool = False


class Budgets(_Model):
    max_calls: int
    max_llm_tokens: int
    wall_clock_s: int


class Manifest(_Model):
    workflow: str
    version: int
    created_at: datetime
    source_traces: list[str]
    skeleton_hash: str
    tools: list[ToolEntry] = Field(default_factory=list[ToolEntry])
    servers: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])
    inputs_schema: dict[str, Any] = Field(default_factory=dict[str, Any])
    output_schema: dict[str, Any] = Field(default_factory=dict[str, Any])
    steps: list[StepSummary] = Field(default_factory=list[StepSummary])
    llm_steps: list[LlmStep] = Field(default_factory=list[LlmStep])
    transforms: list[TransformEntry] = Field(default_factory=list[TransformEntry])
    volatile_paths: dict[int, list[str]] = Field(default_factory=dict[int, list[str]])
    result_schemas: dict[int, dict[str, Any]] = Field(default_factory=dict[int, dict[str, Any]])
    budgets: Budgets
    program_hash: str = ""
    usable: bool = False
    warnings: list[str] = Field(default_factory=list[str])
    recording: dict[str, str] = Field(default_factory=dict[str, str])
    # per source trace: event idx of each kept step's call (-1 when the step is absent)
    trace_calls: dict[str, list[int]] = Field(default_factory=dict[str, list[int]])
    # per source trace: declared + inferred input values used for replay
    trace_inputs: dict[str, dict[str, Any]] = Field(default_factory=dict[str, dict[str, Any]])
    # write-effect steps: a recorded result (volatile paths masked) returned on dry-run
    placeholder_results: dict[int, Any] = Field(default_factory=dict[int, Any])
    # builtin.Bash steps: regex the live command must fully match (recorded template)
    bash_templates: dict[int, str] = Field(default_factory=dict[int, str])

    def tool(self, canonical: str) -> ToolEntry | None:
        for t in self.tools:
            if t.canonical == canonical:
                return t
        return None

    def llm_step(self, step_id: str) -> LlmStep | None:
        for s in self.llm_steps:
            if s.id == step_id:
                return s
        return None


def infer_result_schema(results: list[Any]) -> dict[str, Any]:
    """genson schema over all recorded results of one step (``required`` = keys in every one)."""
    from genson import SchemaBuilder  # pyright: ignore[reportMissingTypeStubs]

    builder: Any = SchemaBuilder()
    for result in results:
        builder.add_object(result)
    return cast(dict[str, Any], builder.to_schema())


def _results_of(step: StepSpec, traces: dict[str, Trace]) -> list[Any]:
    out: list[Any] = []
    for tid, event_idx in step.calls.items():
        call = traces[tid].events[event_idx]
        assert isinstance(call, ToolCall)
        out.append(call.result)
    return out


def tool_entries(skeleton: Skeleton, traces: dict[str, Trace]) -> list[ToolEntry]:
    """Tools used by kept steps, schemas taken from the newest trace that has them."""
    entries: dict[str, ToolEntry] = {}
    for step in skeleton.steps:
        if step.tool in entries:
            continue
        schema = None
        for trace in reversed(list(traces.values())):
            schema = trace.tool_schema(step.tool)
            if schema is not None and schema.input_schema:
                break
        if schema is None:
            server, _, name = step.tool.partition(".")
            entries[step.tool] = ToolEntry(
                canonical=step.tool, server=server, name=name, effect=step.effect
            )
        else:
            entries[step.tool] = ToolEntry(
                canonical=schema.canonical,
                server=schema.server,
                name=schema.name,
                effect=schema.effect if schema.effect != "unknown" else step.effect,
                schema_hash=schema.hash,
                input_schema=dict(schema.input_schema),
            )
    return list(entries.values())


def build_manifest(
    skeleton: Skeleton,
    traces: list[Trace],
    *,
    version: int,
    created_at: datetime,
    budgets: BudgetsCfg,
    inputs_schema: dict[str, Any],
    output_schema: dict[str, Any],
    steps: list[StepSummary],
    llm_steps: list[LlmStep],
    transforms: list[TransformEntry],
    warnings: list[str],
) -> Manifest:
    by_id = {t.id: t for t in traces}
    tools = tool_entries(skeleton, by_id)
    servers: dict[str, list[str]] = {}
    for t in tools:
        servers.setdefault(t.server, []).append(t.name)
    max_recorded = max((len(t.calls) for t in traces), default=0)
    recording: dict[str, str] = {}
    for trace in reversed(traces):
        for key in ("mcp_config", "allow", "permission_mode", "model", "agent_args"):
            if key in trace.harness and key not in recording:
                recording[key] = trace.harness[key]
    return Manifest(
        workflow=skeleton.workflow,
        version=version,
        created_at=created_at,
        source_traces=list(skeleton.traces),
        skeleton_hash=skeleton.hash,
        tools=tools,
        servers=servers,
        inputs_schema=inputs_schema,
        output_schema=output_schema,
        steps=steps,
        llm_steps=llm_steps,
        transforms=transforms,
        volatile_paths=dict(skeleton.volatile_paths),
        result_schemas={s.idx: infer_result_schema(_results_of(s, by_id)) for s in skeleton.steps},
        budgets=Budgets(
            max_calls=max(1, round(budgets.max_calls_factor * max_recorded)),
            max_llm_tokens=budgets.max_llm_tokens,
            wall_clock_s=budgets.wall_clock_s,
        ),
        warnings=list(warnings),
        recording=recording,
    )
