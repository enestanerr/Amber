"""Compile: skeleton → ``program.py`` + ``manifest.json`` + replay tests (``docs/04-compiler.md``).

The compiler is boring on purpose: ``codegen`` emits the program structure deterministically,
``holes``/``synthesizer`` fill typed holes with a model and verify every fill against the
recordings, ``sandbox``/``lint`` keep generated code inside an allow-list, ``versions`` manages
``programs/<workflow>/v<N>/``.
"""

from __future__ import annotations
