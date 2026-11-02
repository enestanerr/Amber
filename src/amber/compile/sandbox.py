"""Restricted execution of generated transform code (``docs/04-compiler.md`` §D4).

AST allow-list (imports limited to ``math, datetime, statistics, re, json, itertools,
collections``; no dunder attributes; no ``exec``/``eval``/``open``/…), a curated builtins
dict, and a 5 s timeout via a worker thread. Known limit: a CPU-bound infinite loop keeps
its thread alive after the timeout (v0.2 moves this to a subprocess).
"""

from __future__ import annotations

import ast
import builtins
import importlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from amber.errors import AmberError

ALLOWED_MODULES = frozenset(
    {"math", "datetime", "statistics", "re", "json", "itertools", "collections"}
)
FORBIDDEN_NAMES = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "open",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "delattr",
        "breakpoint",
        "input",
        "__import__",
        "vars",
    }
)
_SAFE_BUILTIN_NAMES = (
    "len", "range", "min", "max", "sum", "sorted", "enumerate", "zip", "map", "filter",
    "abs", "round", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "any",
    "all", "isinstance", "reversed", "repr", "divmod", "frozenset", "iter", "next",
    "ValueError", "KeyError", "TypeError", "IndexError", "ZeroDivisionError",
    "StopIteration", "Exception", "True", "False", "None",
)  # fmt: skip
DEFAULT_TIMEOUT = 5.0


class SandboxViolation(AmberError):
    exit_code = 1


class SandboxTimeout(AmberError):
    exit_code = 1


def _safe_import(
    name: str,
    globals_: dict[str, Any] | None = None,
    locals_: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    if level != 0 or name.split(".")[0] not in ALLOWED_MODULES:
        raise ImportError(f"import of {name!r} is not allowed in transforms")
    return importlib.import_module(name)


def safe_builtins() -> dict[str, Any]:
    out: dict[str, Any] = {
        n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES if hasattr(builtins, n)
    }
    out["__import__"] = _safe_import
    return out


def check_source(source: str) -> None:
    """Raise :class:`SandboxViolation` listing every disallowed construct in *source*."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise SandboxViolation(f"transform source does not parse: {e.msg} (line {e.lineno})") from e
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    problems.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ALLOWED_MODULES:
                problems.append(f"from {node.module} import …")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"dunder attribute .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"name {node.id}")
        elif isinstance(node, ast.ClassDef):
            problems.append(f"class {node.name} (classes are not allowed in transforms)")
        elif isinstance(node, ast.Global | ast.Nonlocal):
            problems.append("global/nonlocal")
    if problems:
        raise SandboxViolation(
            "transform uses disallowed constructs: " + ", ".join(sorted(set(problems))),
            hint=f"Only {', '.join(sorted(ALLOWED_MODULES))} may be imported; no dunders or I/O.",
        )


def load_function(source: str, name: str) -> Callable[..., Any]:
    """Check and execute *source* in the restricted namespace; return the function *name*."""
    check_source(source)
    namespace: dict[str, Any] = {"__builtins__": safe_builtins(), "__name__": "amber_transform"}
    for module in ALLOWED_MODULES:
        namespace[module] = importlib.import_module(module)
    exec(compile(source, "<transform>", "exec"), namespace)  # this is the sandbox boundary
    fn = namespace.get(name)
    if not callable(fn):
        raise SandboxViolation(f"transform source does not define a function named {name!r}")
    return fn


def call_with_timeout(
    fn: Callable[..., Any], kwargs: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT
) -> Any:
    """Run ``fn(**kwargs)`` in a worker thread; raise :class:`SandboxTimeout` after *timeout*."""
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        raise SandboxTimeout(f"transform exceeded {timeout:.0f} s") from None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
