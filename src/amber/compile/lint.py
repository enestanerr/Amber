"""AST lint for generated ``program.py`` (``docs/04-compiler.md`` §B, generated code rules).

Imports only ``__future__``, ``typing``, ``pydantic``, ``amber.runtime`` and the transform
allow-list; no ``datetime.now``/``random``/``os``/``subprocess``/``open``/``requests``/``httpx``;
no ``try/except`` around ``ctx.tool``.
"""

from __future__ import annotations

import ast

from amber.compile.sandbox import ALLOWED_MODULES
from amber.errors import AmberError

ALLOWED_IMPORT_ROOTS = frozenset({"__future__", "typing", "pydantic"}) | ALLOWED_MODULES
FORBIDDEN_MODULES = frozenset({"random", "os", "subprocess", "requests", "httpx", "sys", "socket"})
FORBIDDEN_CALLS = frozenset({"open", "exec", "eval", "__import__", "input"})


class LintError(AmberError):
    exit_code = 1


def _contains_ctx_tool(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "tool"
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "ctx"
        ):
            return True
    return False


def lint_source(source: str) -> list[str]:
    """Return the list of violations (empty when the program is clean)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "amber":
                    if not alias.name.startswith("amber.runtime"):
                        problems.append(f"import {alias.name}: only amber.runtime is allowed")
                elif root not in ALLOWED_IMPORT_ROOTS:
                    problems.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root == "amber":
                if not module.startswith("amber.runtime"):
                    problems.append(f"from {module} import …: only amber.runtime is allowed")
            elif node.level or root not in ALLOWED_IMPORT_ROOTS:
                problems.append(f"from {module} import …")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                problems.append(f"call to {func.id}()")
            elif isinstance(func, ast.Attribute):
                if func.attr == "now" and _is_datetime(func.value):
                    problems.append("datetime.now() (use ctx.now())")
                base = _root_name(func.value)
                if base in FORBIDDEN_MODULES:
                    problems.append(f"call into {base}.{func.attr}")
        elif isinstance(node, ast.Try) and _contains_ctx_tool(node):
            problems.append("try/except around ctx.tool (guards handle tool errors)")
    return sorted(set(problems))


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_datetime(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "datetime"
    if isinstance(node, ast.Attribute):
        return node.attr == "datetime" or _is_datetime(node.value)
    return False


def lint_program(source: str, *, where: str = "program.py") -> None:
    problems = lint_source(source)
    if problems:
        raise LintError(
            f"{where} violates the generated-code rules: " + "; ".join(problems),
            hint="Programs may import only pydantic, typing and amber.runtime; "
            "see docs/04-compiler.md §B.",
        )
