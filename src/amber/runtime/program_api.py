"""Program API (``docs/04-compiler.md`` §B).

A compiled program is a module with one function decorated ``@program(workflow=..., version=...)``
taking ``(inputs: Inputs, ctx: Context)`` and returning ``Output``. ``Context`` is a sync facade:
the runner drives an event loop; programs stay simple synchronous code.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar, get_type_hints

from pydantic import BaseModel

from amber.errors import AmberError
from amber.util.hashing import sha256_hex

T = TypeVar("T", bound=BaseModel)
PROGRAM_ATTR = "__amber_program__"


def jsonable(value: Any) -> Any:
    """Plain JSON values for a tool call: a judgment can return nested models (a batch of
    ``{number, labels}`` items) that go straight into ``ctx.tool`` arguments."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


class Context(Protocol):
    """What a program may do: call tools, ask for judgment, read the clock, log."""

    def tool(self, name: str, /, **args: Any) -> Any: ...

    def llm(self, step_id: str, schema: type[T], prompt: str, context: dict[str, Any]) -> T: ...

    def now(self) -> datetime: ...

    def log(self, msg: str) -> None: ...


Entry = Callable[[Any, Any], Any]


@dataclass(frozen=True)
class Program:
    workflow: str
    version: int
    entry: Entry
    inputs_model: type[BaseModel]
    output_model: type[BaseModel]
    path: Path | None = None

    def parse_inputs(self, raw: dict[str, Any]) -> BaseModel:
        return self.inputs_model.model_validate(raw)


class ProgramLoadError(AmberError):
    exit_code = 1


def program(*, workflow: str, version: int) -> Callable[[Entry], Entry]:
    """Register the entry point and capture its ``Inputs``/``Output`` models from annotations."""

    def decorate(fn: Entry) -> Entry:
        hints = get_type_hints(fn)
        code = fn.__code__
        params = list(code.co_varnames[: code.co_argcount])
        if len(params) != 2:
            raise ProgramLoadError(
                f"@program entry {fn.__name__} must take (inputs, ctx), got {params}"
            )
        inputs_model = hints.get(params[0])
        output_model = hints.get("return")
        for label, model in (("inputs", inputs_model), ("return", output_model)):
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                raise ProgramLoadError(
                    f"@program entry {fn.__name__}: {label} annotation must be a pydantic model"
                )
        assert isinstance(inputs_model, type) and isinstance(output_model, type)
        setattr(
            fn,
            PROGRAM_ATTR,
            Program(
                workflow=workflow,
                version=version,
                entry=fn,
                inputs_model=inputs_model,
                output_model=output_model,
            ),
        )
        return fn

    return decorate


def load_program(path: Path) -> Program:
    """Import ``program.py`` from *path* and return its registered :class:`Program`."""
    path = path.resolve()
    if not path.is_file():
        raise ProgramLoadError(f"program not found: {path}", hint="Run `amber compile` first.")
    name = f"amber_program_{sha256_hex(str(path))[:12]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - importlib contract
        raise ProgramLoadError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ProgramLoadError(f"{path.name} failed to import: {type(e).__name__}: {e}") from e
    for obj in vars(module).values():
        prog = getattr(obj, PROGRAM_ATTR, None)
        if isinstance(prog, Program):
            return dataclasses.replace(prog, path=path)
    raise ProgramLoadError(f"{path} defines no @program entry point")
