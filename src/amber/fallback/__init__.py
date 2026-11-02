"""Fallback loop: proposal → sentry (veto) → arbiter → agent fallback → recompile
(``docs/05-runtime.md`` §5). ``agent_fallback`` is the only module here that knows about Claude."""

from __future__ import annotations
