"""Runtime: the program API used by generated code, backends, guards, ledger and runner.

Generated programs import only from here (``from amber.runtime import Context, program``).
Nothing in this package knows about Claude; model access goes through ``amber.models``
behind the ``ModelBackend`` protocol.
"""

from __future__ import annotations

from amber.runtime.program_api import Context, Program, load_program, program

__all__ = ["Context", "Program", "load_program", "program"]
