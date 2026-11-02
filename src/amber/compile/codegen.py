"""Deterministic code generation (``docs/04-compiler.md`` §C).

Same skeleton (and same fills) → byte-identical ``program.py``. Variables: ``r<idx>`` tool
results, ``t<idx>`` transform outputs, ``s<idx>`` judgment outputs, ``g<idx>`` gates.
"""

from __future__ import annotations

import ast
import json
import keyword
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from amber.align.models import Predicate, Provenance, Skeleton, StepSpec
from amber.compile.holes import Hole
from amber.compile.manifest import FieldSpec, LlmStep, StepSummary, TransformEntry
from amber.errors import AmberError
from amber.trace.jsonpath import Wildcard, parse_path
from amber.trace.models import ToolCall, Trace

TEMPLATES = Path(__file__).parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)
TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list[Any]",
    "object": "dict[str, Any]",
}
MAX_OUTPUT_FIELDS = 6
PROMPT_WRAP = 88


class CodegenError(AmberError):
    exit_code = 1


@dataclass
class Fills:
    """Accepted hole fills: transform sources and judgment/gate prompts + fields."""

    transforms: dict[str, str] = field(default_factory=dict[str, str])
    judgments: dict[str, tuple[str, list[FieldSpec]]] = field(
        default_factory=dict[str, tuple[str, list[FieldSpec]]]
    )


@dataclass
class Generated:
    source: str
    test_source: str
    steps: list[StepSummary]
    llm_steps: list[LlmStep]
    transforms: list[TransformEntry]


# --- expressions --------------------------------------------------------------


def ident(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", name) or "value"
    if out[0].isdigit():
        out = "in_" + out
    if keyword.iskeyword(out):
        out += "_"
    return out


def py_literal(value: Any) -> str:
    """Python literal for a JSON value (strings double-quoted like the rest of the program)."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool) or value is None or isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(py_literal(v) for v in cast(list[Any], value)) + "]"
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        return "{" + ", ".join(f"{py_literal(str(k))}: {py_literal(v)}" for k, v in d.items()) + "}"
    return repr(value)


def path_expr(var: str, path: str) -> str:
    out = var
    for part in parse_path(path):
        if isinstance(part, Wildcard):
            raise CodegenError(f"wildcard path {path!r} cannot be used as an expression")
        out += f"[{part}]" if isinstance(part, int) else f"[{json.dumps(part)}]"
    return out


def _fstring_literal(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "{{").replace("}", "}}").replace('"', '\\"')


class _Holes:
    def __init__(self, holes: list[Hole]) -> None:
        self.by_step: dict[int, dict[str, Hole]] = {}
        for h in holes:
            self.by_step.setdefault(h.step_idx, {})[h.kind] = h

    def get(self, step_idx: int, kind: str) -> Hole | None:
        return self.by_step.get(step_idx, {}).get(kind)

    def field_for(self, step_idx: int, kind: str, path: str) -> str:
        hole = self.get(step_idx, kind)
        if hole is None:
            raise CodegenError(f"no {kind} hole for step {step_idx} ({path})")
        for name, p in hole.fields.items():
            if p == path:
                return name
        raise CodegenError(f"{kind} hole of step {step_idx} does not produce {path}")


def prov_expr(prov: Provenance, step: StepSpec, path: str, holes: _Holes) -> str:
    if prov.kind == "input":
        return f"inputs.{ident(prov.name or 'value')}"
    if prov.kind == "ref":
        assert prov.col is not None and prov.path is not None
        return path_expr(f"r{prov.col}", prov.path)
    if prov.kind == "template":
        text = ""
        for part in prov.parts or []:
            if part.literal is not None:
                text += _fstring_literal(part.literal)
            elif part.ref is not None:
                text += "{" + prov_expr(part.ref, step, path, holes) + "}"
        return f'f"{text}"'
    if prov.kind == "const":
        return py_literal(prov.value)
    if prov.hole == "transform":
        return f"t{step.idx}[{json.dumps(holes.field_for(step.idx, 'transform', path))}]"
    return f"s{step.idx}.{holes.field_for(step.idx, 'judgment', path)}"


# --- argument reconstruction --------------------------------------------------


class _Leaf:
    def __init__(self, expr: str) -> None:
        self.expr = expr


def _insert(tree: dict[Any, Any], parts: tuple[Any, ...], expr: str) -> None:
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = _Leaf(expr)


def _render(node: Any) -> str:
    if isinstance(node, _Leaf):
        return node.expr
    d = cast(dict[Any, Any], node)
    if d and all(isinstance(k, int) for k in d):
        return "[" + ", ".join(_render(d[k]) for k in sorted(d)) + "]"
    return "{" + ", ".join(f"{json.dumps(str(k))}: {_render(v)}" for k, v in d.items()) + "}"


def render_args(step: StepSpec, holes: _Holes) -> str:
    tree: dict[Any, Any] = {}
    for path, prov in step.args.items():
        parts = parse_path(path)
        if not parts:  # "$": the whole args object (only for empty args)
            continue
        _insert(tree, parts, prov_expr(prov, step, path, holes))
    kwargs: list[str] = []
    extra: list[str] = []
    for key, node in tree.items():
        text = _render(node)
        if isinstance(key, str) and key.isidentifier() and not keyword.iskeyword(key):
            kwargs.append(f"{key}={text}")
        else:
            extra.append(f"{json.dumps(str(key))}: {text}")
    if extra:
        kwargs.append("**{" + ", ".join(extra) + "}")
    return ", ".join(kwargs)


# --- models -------------------------------------------------------------------


def _py_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list[Any]"
    if isinstance(value, dict):
        return "dict[str, Any]"
    return "Any"


def inputs_class(skeleton: Skeleton) -> str:
    lines = ["class Inputs(BaseModel):", '    model_config = ConfigDict(extra="forbid")']
    for name, spec in skeleton.inputs.items():
        desc = "Declared input" if spec.source == "declared" else "Inferred from the recordings"
        examples = json.dumps(spec.examples[:4], ensure_ascii=False)
        lines.append(
            f"    {ident(name)}: {TYPE_MAP.get(spec.type, 'str')} = Field("
            f"description={json.dumps(desc)}, examples={examples})"
        )
    return "\n".join(lines)


def _final_result(skeleton: Skeleton, traces: dict[str, Trace]) -> tuple[StepSpec, Any]:
    last = skeleton.steps[-1]
    tid = next(iter(last.calls))
    call = traces[tid].events[last.calls[tid]]
    assert isinstance(call, ToolCall)
    return last, call.result


def output_fields(
    skeleton: Skeleton, traces: dict[str, Trace], holes: list[Hole]
) -> list[tuple[str, str, str]]:
    """``(name, type, expression)`` for the Output model (§C)."""
    last, result = _final_result(skeleton, traces)
    var = f"r{last.idx}"
    fields: list[tuple[str, str, str]] = []
    optional = last.conditional
    if isinstance(result, dict):
        d = cast(dict[str, Any], result)
        for key, value in d.items():
            if isinstance(value, dict | list) or len(fields) >= MAX_OUTPUT_FIELDS:
                continue
            t = _py_type(value)
            expr = (
                path_expr(var, f"$.{key}") if key.isidentifier() else path_expr(var, f'$["{key}"]')
            )
            if optional:
                t, expr = f"{t} | None", f"None if {var} is None else {expr}"
            fields.append((ident(key), t, expr))
    elif isinstance(result, str):
        fields.append(("result", "str | None" if optional else "str", var))
    elif isinstance(result, list):
        fields.append(("result", "list[Any] | None" if optional else "list[Any]", var))
    elif result is not None:
        fields.append(("result", "Any", var))
    taken = {f[0] for f in fields}
    for hole in holes:
        if hole.kind != "judgment":
            continue
        for name, ftype in hole.expected_types.items():
            if ftype != "str":
                continue
            out_name = name if name not in taken else f"{name}_{hole.step_idx}"
            taken.add(out_name)
            fields.append((out_name, "str", f"s{hole.step_idx}.{name}"))
    if not fields:
        fields.append(("ok", "bool", "True"))
    return fields


def output_class(fields: list[tuple[str, str, str]]) -> str:
    lines = ["class Output(BaseModel):", '    model_config = ConfigDict(extra="forbid")']
    lines += [f"    {name}: {ftype}" for name, ftype, _ in fields]
    return "\n".join(lines)


# --- holes --------------------------------------------------------------------


def render_prompt(name: str, text: str) -> str:
    """``NAME = (`` + one double-quoted chunk per line, wrapped at ~88 chars + ``)``."""
    if not text:
        return f'{name} = ""'
    chunks: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n")
        ending = line[len(body) :]
        wrapped = textwrap.wrap(
            body, PROMPT_WRAP, break_long_words=False, drop_whitespace=False
        ) or [""]
        for i, piece in enumerate(wrapped):
            chunks.append(piece + (ending if i == len(wrapped) - 1 else ""))
    rendered = "\n".join("    " + json.dumps(c, ensure_ascii=False) for c in chunks)
    return f"{name} = (\n{rendered}\n)"


def judgment_class(name: str, fields: list[FieldSpec]) -> str:
    lines = [f"class {name}(BaseModel):", '    model_config = ConfigDict(extra="forbid")']
    for f in fields:
        if f.enum:
            ftype = "Literal[" + ", ".join(json.dumps(v) for v in f.enum) + "]"
        else:
            ftype = f.type
        lines.append(
            f"    {ident(f.name)}: {ftype} = Field(description={json.dumps(f.description)})"
        )
    if not any(f.name == "confidence" for f in fields):
        lines.append("    confidence: float = Field(ge=0, le=1)")
    return "\n".join(lines)


def split_imports(source: str) -> tuple[list[str], str]:
    """Hoist top-level imports out of a transform fill so they sit in the module header."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], source
    lines = source.splitlines()
    imports: list[str] = []
    drop: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            start, end = node.lineno - 1, (node.end_lineno or node.lineno) - 1
            imports.append("\n".join(lines[start : end + 1]).strip())
            drop.update(range(start, end + 1))
    rest = "\n".join(line for i, line in enumerate(lines) if i not in drop)
    return imports, rest.strip("\n")


def hole_blocks(holes: list[Hole], fills: Fills) -> tuple[list[str], list[str]]:
    """Return ``(blocks, hoisted_imports)`` in step order (gate, transform, judgment)."""
    blocks: list[str] = []
    imports: list[str] = []
    order = {"gate": 0, "transform": 1, "judgment": 2}
    for hole in sorted(holes, key=lambda h: (h.step_idx, order[h.kind])):
        produces = ", ".join(hole.fields)
        header = f"# AMBER-HOLE:{hole.id} — {hole.kind} for {hole.tool} ({produces})"
        if hole.kind == "transform":
            source = fills.transforms.get(hole.id)
            if source is None:
                source = (
                    f"{hole.signature}:\n"
                    f'    raise NotImplementedError("hole {hole.id} is not filled yet")'
                )
            hoisted, source = split_imports(source)
            imports.extend(hoisted)
            blocks.append(header + "\n" + source)
            continue
        prompt, fields = fills.judgments.get(hole.id, (None, None))
        if fields is None:
            fields = [
                FieldSpec(
                    name=n, type=hole.expected_types.get(n, "str"), description=f"value of {n}"
                )
                for n in hole.fields
            ]
        prompt_text = prompt if prompt is not None else f"TODO: fill hole {hole.id}"
        blocks.append(
            header
            + "\n"
            + judgment_class(hole.schema_name, fields)
            + "\n\n\n"
            + render_prompt(hole.prompt_name, prompt_text)
        )
    return blocks, imports


# --- body ---------------------------------------------------------------------


def predicate_expr(pred: Predicate) -> str:
    assert pred.col is not None and pred.path is not None
    ref = path_expr(f"r{pred.col}", pred.path)
    if pred.op == "non-empty":
        return f"bool({ref})"
    return f"{ref} {pred.op} {py_literal(pred.value)}"


def _context_literal(keys: list[str]) -> str:
    items = [
        '"inputs": inputs.model_dump()' if k == "inputs" else f"{json.dumps(k)}: {k}" for k in keys
    ]
    return "{" + ", ".join(items) + "}"


def body_lines(
    skeleton: Skeleton, holes: list[Hole], out_fields: list[tuple[str, str, str]]
) -> list[str]:
    hb = _Holes(holes)
    lines: list[str] = []
    for step in skeleton.steps:
        inner: list[str] = []
        gate = hb.get(step.idx, "gate")
        cond: str | None = None
        if step.conditional:
            if gate is not None:
                lines.append(
                    f'g{step.idx} = ctx.llm("{gate.llm_step_id}", {gate.schema_name}, '
                    f"{gate.prompt_name}, context={_context_literal(gate.context_keys)})"
                    f"  # gate for step {step.idx + 1}"
                )
                cond = f"g{step.idx}.go"
            elif step.predicate is not None and step.predicate.kind == "data":
                cond = predicate_expr(step.predicate)
        transform = hb.get(step.idx, "transform")
        if transform is not None:
            # transforms are verified against the recordings with ``inputs`` as a plain dict
            # (``trace.inputs``), so they must be called with one too
            call_args = ", ".join(
                "inputs.model_dump()" if p == "inputs" else p for p in transform.params
            )
            inner.append(f"t{step.idx} = {transform.function_name}({call_args})  # transform hole")
        judgment = hb.get(step.idx, "judgment")
        if judgment is not None:
            inner.append(
                f's{step.idx} = ctx.llm("{judgment.llm_step_id}", {judgment.schema_name}, '
                f"{judgment.prompt_name}, context={_context_literal(judgment.context_keys)})"
                "  # judgment"
            )
        effect = "  # effect: write" if step.effect == "write" else ""
        args = render_args(step, hb)
        call = f"r{step.idx} = ctx.tool({json.dumps(step.tool)}" + (f", {args})" if args else ")")
        inner.append(f"{call}  # step {step.idx + 1}{effect}")
        if cond is not None:
            lines.append(f"r{step.idx} = None")
            lines.append(f"if {cond}:")
            lines.extend("    " + line for line in inner)
        else:
            lines.extend(inner)
    lines.append("return Output(" + ", ".join(f"{n}={e}" for n, _, e in out_fields) + ")")
    return lines


# --- entry --------------------------------------------------------------------


def generate(
    skeleton: Skeleton,
    traces: list[Trace],
    holes: list[Hole],
    fills: Fills,
    *,
    version: int,
) -> Generated:
    if not skeleton.steps:
        raise CodegenError("the skeleton has no steps; nothing to compile")
    by_id = {t.id: t for t in traces}
    out_fields = output_fields(skeleton, by_id, holes)
    blocks, hoisted = hole_blocks(holes, fills)
    inputs_src = inputs_class(skeleton)
    output_src = output_class(out_fields)
    body = body_lines(skeleton, holes, out_fields)
    rendered_parts = "\n".join([inputs_src, output_src, *blocks, *body])
    typing_names = [n for n in ("Any", "Literal") if re.search(rf"\b{n}\b", rendered_parts)]
    stdlib = sorted(set(hoisted))
    if typing_names:
        stdlib.append("from typing import " + ", ".join(typing_names))
    source = _ENV.get_template("program.py.j2").render(
        workflow=skeleton.workflow,
        version=version,
        n_traces=len(skeleton.traces),
        skeleton_hash=skeleton.hash,
        stdlib_imports=stdlib,
        inputs_class=inputs_src,
        output_class=output_src,
        hole_blocks=blocks,
        body=body,
    )
    test_source = _ENV.get_template("test_replay.py.j2").render(
        workflow=skeleton.workflow, version=version, traces=list(skeleton.traces)
    )
    steps = [
        StepSummary(
            idx=s.idx,
            tool=s.tool,
            kind=s.kind,
            conditional=s.conditional,
            effect=s.effect,
            var=f"r{s.idx}",
            observe=s.observe,
        )
        for s in skeleton.steps
    ]
    llm_steps: list[LlmStep] = []
    transforms: list[TransformEntry] = []
    for hole in holes:
        if hole.kind == "transform":
            transforms.append(
                TransformEntry(
                    id=hole.id,
                    step_idx=hole.step_idx,
                    function=hole.function_name,
                    produces=list(hole.fields),
                )
            )
            continue
        _, fields = fills.judgments.get(hole.id, (None, None))
        if fields is None:
            fields = [
                FieldSpec(name=n, type=hole.expected_types.get(n, "str")) for n in hole.fields
            ]
        llm_steps.append(
            LlmStep(
                id=hole.llm_step_id,
                step_idx=hole.step_idx,
                kind="gate" if hole.kind == "gate" else "judgment",
                schema_name=hole.schema_name,
                fields=list(fields),
                context_keys=list(hole.context_keys),
                field_paths={} if hole.kind == "gate" else dict(hole.fields),
            )
        )
    return Generated(
        source=source,
        test_source=test_source,
        steps=steps,
        llm_steps=llm_steps,
        transforms=transforms,
    )
