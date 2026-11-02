"""Guards on every live call (``docs/05-runtime.md`` §3).

``ToolAllowlistGuard`` · ``SchemaDriftGuard`` · ``ResultShapeGuard`` · ``BudgetGuard`` ·
``WriteEffectGuard`` (dry-run placeholder) · ``LLMOutputGuard`` · ``ErrorGuard``. Hard stops
raise :class:`GuardStop` (exit 4); soft events are ledgered and feed the doctor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel

from amber.compile.manifest import Manifest
from amber.errors import AmberError
from amber.trace.canonical import canonicalize_args
from amber.trace.models import Usage

Severity = Literal["info", "soft", "hard"]
PROCEED = object()


@dataclass(frozen=True)
class GuardEvent:
    guard: str
    severity: Severity
    detail: str
    step_idx: int


class GuardStop(AmberError):
    exit_code = 4

    def __init__(
        self, kind: str, detail: str, *, step_idx: int, severity: Severity = "hard"
    ) -> None:
        self.kind = kind
        self.detail = detail
        self.step_idx = step_idx
        self.severity = severity
        super().__init__(
            f"guard {kind} stopped the run at step {step_idx + 1}: {detail}",
            hint="See `amber doctor <workflow>` for a diagnosis and the recommended action.",
        )


# --- result-shape validation (the genson subset) -----------------------------


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _types_of(schema: dict[str, Any]) -> set[str]:
    t = schema.get("type")
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {str(x) for x in cast(list[Any], t)}
    if "anyOf" in schema:
        out: set[str] = set()
        for sub in cast(list[dict[str, Any]], schema["anyOf"]):
            out |= _types_of(sub)
        return out
    return set()


def shape_problems(
    schema: dict[str, Any], value: Any, path: str = "$"
) -> list[tuple[Severity, str]]:
    """Compare *value* with a genson schema: missing required/type change → hard, extras → soft."""
    problems: list[tuple[Severity, str]] = []
    allowed = _types_of(schema)
    actual = _json_type(value)
    if allowed and actual not in allowed and not (actual == "integer" and "number" in allowed):
        problems.append(("hard", f"{path}: type {actual}, recorded {'/'.join(sorted(allowed))}"))
        return problems
    if "anyOf" in schema:
        subs = cast(list[dict[str, Any]], schema["anyOf"])
        matching = [s for s in subs if actual in _types_of(s)]
        if matching:
            schema = matching[0]
    if actual == "object":
        obj = cast(dict[str, Any], value)
        props = cast(dict[str, Any], schema.get("properties") or {})
        for key in cast(list[str], schema.get("required") or []):
            if key not in obj:
                problems.append(("hard", f"{path}: missing key {key!r}"))
        for key in obj:
            if props and key not in props:
                problems.append(("soft", f"{path}: extra key {key!r}"))
        for key, sub in props.items():
            if key in obj:
                problems.extend(shape_problems(sub, obj[key], f"{path}.{key}"))
    elif actual == "array":
        items = cast(list[Any], value)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            if not items:
                problems.append(("soft", f"{path}: empty list where recordings had items"))
            for i, item in enumerate(items[:20]):
                problems.extend(
                    shape_problems(cast(dict[str, Any], item_schema), item, f"{path}[{i}]")
                )
    return problems


# --- the guard set -------------------------------------------------------------


@dataclass
class Guards:
    manifest: Manifest
    allow_writes: bool = False
    min_confidence: float = 0.5
    wall_clock_s: float | None = None
    events: list[GuardEvent] = field(default_factory=list[GuardEvent])
    calls: int = 0
    llm_tokens: int = 0
    started: float = field(default_factory=time.monotonic)

    def record(self, guard: str, severity: Severity, detail: str, step_idx: int) -> GuardEvent:
        event = GuardEvent(guard, severity, detail, step_idx)
        self.events.append(event)
        return event

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float | None:
        limit = (
            self.wall_clock_s
            if self.wall_clock_s is not None
            else self.manifest.budgets.wall_clock_s
        )
        return max(0.0, limit - self.elapsed())

    # SchemaDriftGuard: evaluated once after the live backend listed its tools.
    def drift(self, events: list[GuardEvent]) -> None:
        for e in events:
            self.events.append(e)
        hard = [e for e in events if e.severity == "hard"]
        if hard:
            raise GuardStop("schema_drift", hard[0].detail, step_idx=hard[0].step_idx)

    def before_call(self, step_idx: int, tool: str, args: dict[str, Any]) -> Any:
        """Return :data:`PROCEED` or a dry-run placeholder result."""
        entry = self.manifest.tool(tool)
        if entry is None:
            raise GuardStop("allowlist", f"tool {tool!r} is not in the manifest", step_idx=step_idx)
        self.calls += 1
        if self.calls > self.manifest.budgets.max_calls:
            raise GuardStop(
                "budget",
                f"call budget exceeded ({self.manifest.budgets.max_calls})",
                step_idx=step_idx,
            )
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            raise GuardStop("budget", "wall-clock budget exceeded", step_idx=step_idx)
        if entry.effect != "read" and not self.allow_writes:
            self.record(
                "write_effect",
                "info",
                f"dry-run: {tool} not executed (effect {entry.effect})",
                step_idx,
            )
            placeholder = self.manifest.placeholder_results.get(step_idx)
            return (
                canonicalize_args(placeholder, self.manifest.volatile_paths.get(step_idx, []))
                if isinstance(placeholder, dict)
                else placeholder
            )
        return PROCEED

    def after_call(
        self, step_idx: int, tool: str, result: Any, *, is_error: bool, error_text: str = ""
    ) -> None:
        if is_error:
            raise GuardStop(
                "error", f"{tool} returned an error: {error_text[:300]}", step_idx=step_idx
            )
        schema = self.manifest.result_schemas.get(step_idx)
        if not schema:
            return
        problems = shape_problems(schema, result)
        hard = [p for p in problems if p[0] == "hard"]
        for severity, detail in problems[:10]:
            self.record("result_shape", severity, detail, step_idx)
        if hard:
            raise GuardStop("result_shape", hard[0][1], step_idx=step_idx)

    def after_llm(self, step_idx: int, output: BaseModel, usage: Usage) -> None:
        self.llm_tokens += usage.total_input + usage.output_tokens
        if self.llm_tokens > self.manifest.budgets.max_llm_tokens:
            raise GuardStop(
                "budget",
                f"llm token budget exceeded ({self.manifest.budgets.max_llm_tokens})",
                step_idx=step_idx,
            )
        confidence = getattr(output, "confidence", None)
        if isinstance(confidence, int | float) and confidence < self.min_confidence:
            self.record(
                "llm_output",
                "soft",
                f"confidence {confidence:.2f} < {self.min_confidence}",
                step_idx,
            )

    def llm_invalid(self, step_idx: int, detail: str) -> GuardStop:
        return GuardStop(
            "llm_output", f"model output failed validation: {detail[:300]}", step_idx=step_idx
        )
