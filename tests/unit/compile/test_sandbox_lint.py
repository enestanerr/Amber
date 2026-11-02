"""Sandbox AST rejection list, restricted execution, timeout; program lint rules."""

from __future__ import annotations

import pytest

from amber.compile.lint import LintError, lint_program, lint_source
from amber.compile.sandbox import (
    SandboxTimeout,
    SandboxViolation,
    call_with_timeout,
    check_source,
    load_function,
)


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef f():\n    return os.getcwd()",
        "from pathlib import Path\ndef f():\n    return 1",
        "def f():\n    return open('x').read()",
        "def f():\n    return eval('1')",
        "def f():\n    return (1).__class__",
        "def f():\n    return __import__('os')",
        "class X:\n    pass\ndef f():\n    return X()",
        "def f():\n    global g\n    g = 1",
        "from . import sibling\ndef f():\n    return 1",
        # the typing module itself evaluates annotation strings (get_type_hints)
        "import typing\ndef f():\n    return 1",
        "from typing import get_type_hints\ndef f():\n    return 1",
        "from typing import Any, get_type_hints\ndef f():\n    return 1",
    ],
)
def test_check_source_rejects(source: str) -> None:
    with pytest.raises(SandboxViolation):
        check_source(source)


def test_check_source_rejects_syntax_errors() -> None:
    with pytest.raises(SandboxViolation, match="does not parse"):
        check_source("def f(:\n  pass")


def test_load_function_with_allowed_imports() -> None:
    src = (
        "from datetime import date, timedelta\nimport math\n\n"
        "def transform_step3(inputs, r0, r1, r2):\n"
        "    start = date.fromisoformat(r0['start'])\n"
        "    return {'as_of': (start - timedelta(days=1)).isoformat(),"
        " 'pi': math.floor(math.pi)}\n"
    )
    fn = load_function(src, "transform_step3")
    out = call_with_timeout(
        fn, {"inputs": {}, "r0": {"start": "2026-10-12"}, "r1": None, "r2": None}
    )
    assert out == {"as_of": "2026-10-11", "pi": 3}


def test_type_only_imports_are_allowed() -> None:
    """What the synthesizer habitually writes; a live compile lost a repair round to it."""
    src = (
        "from __future__ import annotations\n"
        "from typing import Any, Dict\n"
        "import datetime\n\n"
        "def transform_step1(inputs: Dict[str, Any], r0: Any) -> Dict[str, Any]:\n"
        "    y, w = inputs['week'].split('-W')\n"
        "    d = datetime.date.fromisocalendar(int(y), int(w), 1) - datetime.timedelta(days=7)\n"
        "    iso = d.isocalendar()\n"
        "    return {'week': f'{iso[0]}-W{iso[1]:02d}'}\n"
    )
    fn = load_function(src, "transform_step1")
    assert call_with_timeout(fn, {"inputs": {"week": "2026-W01"}, "r0": {}}) == {"week": "2025-W52"}
    import_fn = fn.__globals__["__builtins__"]["__import__"]
    with pytest.raises(ImportError):
        import_fn("typing")  # no fromlist: the module object itself
    with pytest.raises(ImportError):
        import_fn("typing", None, None, ("get_type_hints",), 0)


def test_load_function_requires_the_named_function() -> None:
    with pytest.raises(SandboxViolation, match="does not define"):
        load_function("def other():\n    return 1", "transform_step0")


def test_runtime_import_of_disallowed_module_is_blocked() -> None:
    # The AST check catches static imports; the restricted __import__ is defense in depth.
    fn = load_function("def f():\n    return 1", "f")
    import_fn = fn.__globals__["__builtins__"]["__import__"]
    with pytest.raises(ImportError):
        import_fn("os")
    assert import_fn("math").pi > 3


def test_safe_builtins_exclude_open() -> None:
    fn = load_function("def f():\n    return sorted(set([3, 1, 2]))", "f")
    assert fn() == [1, 2, 3]
    assert "open" not in fn.__globals__["__builtins__"]


def test_call_with_timeout() -> None:
    # A bytecode loop (not one C call) so the worker thread releases the GIL and the timeout fires.
    src = "def f(n):\n    x = 0\n    for i in range(n):\n        x += i\n    return x"
    fn = load_function(src, "f")
    assert call_with_timeout(fn, {"n": 10}) == 45
    with pytest.raises(SandboxTimeout):
        call_with_timeout(fn, {"n": 10**7}, timeout=0.05)


GOOD = """
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from amber.runtime import Context, program


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool


@program(workflow="w", version=1)
def run(inputs: Inputs, ctx: Context) -> Output:
    r0 = ctx.tool("shop.list", n=1)
    return Output(ok=bool(r0))
"""


def test_lint_accepts_generated_shape() -> None:
    assert lint_source(GOOD) == []
    lint_program(GOOD)


@pytest.mark.parametrize(
    ("snippet", "needle"),
    [
        ("import os\n", "import os"),
        ("from amber.compile import codegen\n", "only amber.runtime"),
        ("import httpx\n", "import httpx"),
        ("from datetime import datetime\nnow = datetime.now()\n", "datetime.now()"),
        ("data = open('x').read()\n", "open()"),
        ("import random\nx = random.random()\n", "random"),
    ],
)
def test_lint_rejects(snippet: str, needle: str) -> None:
    problems = lint_source(GOOD + snippet)
    assert any(needle in p for p in problems), problems


def test_lint_rejects_try_around_ctx_tool() -> None:
    bad = GOOD.replace(
        '    r0 = ctx.tool("shop.list", n=1)\n',
        '    try:\n        r0 = ctx.tool("shop.list", n=1)\n'
        "    except Exception:\n        r0 = None\n",
    )
    with pytest.raises(LintError, match="try/except"):
        lint_program(bad)
