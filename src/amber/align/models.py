"""Skeleton models (``docs/04-compiler.md`` §A7).

A skeleton is the compiler's input: ordered steps with provenance-annotated arguments,
each classified ``deterministic`` | ``judgment`` | ``transform-needed`` (optionally
``conditional``), plus pruned steps, inferred inputs, volatile result paths and warnings.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from amber.util.hashing import digest

ProvKind = Literal["input", "ref", "template", "const", "unknown"]
HoleKind = Literal["transform", "judgment"]
StepKind = Literal["deterministic", "judgment", "transform-needed"]
PredicateOp = Literal["==", "!=", ">", "<", "non-empty"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplatePart(_Model):
    """Either a literal chunk or a reference (``Input``/``Ref`` provenance)."""

    literal: str | None = None
    ref: Provenance | None = None


class Provenance(_Model):
    kind: ProvKind
    name: str | None = None  # Input
    col: int | None = None  # Ref (step index)
    path: str | None = None  # Ref (result path)
    parts: list[TemplatePart] | None = None  # Template
    value: Any | None = None  # Const
    large: bool = False  # Unknown with a ≥ 20 KB value
    hole: HoleKind | None = None  # Unknown: which hole kind produces it

    def refs(self) -> list[int]:
        """Step indexes this provenance depends on."""
        if self.kind == "ref" and self.col is not None:
            return [self.col]
        if self.kind == "template" and self.parts:
            out: list[int] = []
            for part in self.parts:
                if part.ref is not None:
                    out.extend(part.ref.refs())
            return out
        return []

    def ref_paths(self) -> list[tuple[int, str]]:
        if self.kind == "ref" and self.col is not None and self.path is not None:
            return [(self.col, self.path)]
        if self.kind == "template" and self.parts:
            out: list[tuple[int, str]] = []
            for part in self.parts:
                if part.ref is not None:
                    out.extend(part.ref.ref_paths())
            return out
        return []

    def remap(self, mapping: dict[int, int]) -> Provenance:
        """Return a copy with step indexes renumbered (after pruning)."""
        if self.kind == "ref" and self.col is not None:
            return self.model_copy(update={"col": mapping.get(self.col, self.col)})
        if self.kind == "template" and self.parts:
            parts = [
                TemplatePart(literal=p.literal, ref=p.ref.remap(mapping) if p.ref else None)
                for p in self.parts
            ]
            return self.model_copy(update={"parts": parts})
        return self

    def describe(self) -> str:
        """Short human form used by ``amber align --show``."""
        if self.kind == "input":
            return f"Input({self.name})"
        if self.kind == "ref":
            return f"Ref({self.col},{self.path})"
        if self.kind == "template":
            bits: list[str] = []
            for part in self.parts or []:
                if part.literal is not None:
                    bits.append(repr(part.literal))
                elif part.ref is not None:
                    bits.append(part.ref.describe())
            return "Template(" + "+".join(bits) + ")"
        if self.kind == "const":
            text = repr(self.value)
            return f"Const({text if len(text) <= 24 else text[:21] + '...'})"
        return f"Unknown({self.hole or '?'})" + (" large" if self.large else "")


class Predicate(_Model):
    """Why a conditional step runs: a data test on an earlier result, or a judgment gate."""

    kind: Literal["data", "unknown"]
    col: int | None = None
    path: str | None = None
    op: PredicateOp | None = None
    value: Any | None = None

    def describe(self) -> str:
        if self.kind == "unknown":
            return "gate ← llm"
        if self.op == "non-empty":
            return f"Ref({self.col},{self.path}) non-empty"
        return f"Ref({self.col},{self.path}) {self.op} {self.value!r}"


class StepSpec(_Model):
    idx: int
    tool: str
    kind: StepKind = "deterministic"
    conditional: bool = False
    predicate: Predicate | None = None
    args: dict[str, Provenance] = Field(default_factory=dict)  # flattened by JSON path
    present_in: list[str] = Field(default_factory=list)
    calls: dict[str, int] = Field(default_factory=dict)  # trace_id → event idx of the call
    observe: bool = False
    effect: str = "unknown"
    schema_hash: str = ""
    context_hint: list[int] = Field(default_factory=list[int])
    thoughts: dict[str, str] = Field(default_factory=dict)  # trace_id → thoughts before
    pruned_reason: str | None = None

    def unknown_leaves(self) -> dict[str, Provenance]:
        return {p: v for p, v in self.args.items() if v.kind == "unknown"}

    def referenced_steps(self) -> set[int]:
        out: set[int] = set()
        for prov in self.args.values():
            out.update(prov.refs())
        if self.predicate and self.predicate.kind == "data" and self.predicate.col is not None:
            out.add(self.predicate.col)
        return out


class InputSpec(_Model):
    type: str = "string"
    source: Literal["declared", "inferred"] = "declared"
    examples: list[Any] = Field(default_factory=list)


class Skeleton(_Model):
    workflow: str
    traces: list[str]
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    steps: list[StepSpec] = Field(default_factory=list[StepSpec])
    pruned: list[StepSpec] = Field(default_factory=list[StepSpec])
    edges: list[tuple[int, int]] = Field(default_factory=list[tuple[int, int]])
    volatile_paths: dict[int, list[str]] = Field(default_factory=dict[int, list[str]])
    warnings: list[str] = Field(default_factory=list)
    hash: str = ""

    def compute_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"hash"})
        return digest(data, length=16)

    def step(self, idx: int) -> StepSpec:
        return self.steps[idx]
