"""Model ID resolution (``docs/11-decisions.md`` D5).

``amber init`` asks the API for the newest Sonnet-class and Haiku-class models when an API key
is present; otherwise it writes the documented defaults and marks them ``# verify``.
Model IDs are never hardcoded anywhere else — everything reads ``amber.toml``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from amber.util.logging import get_logger

log = get_logger("models.ids")

DEFAULT_SYNTHESIS_MODEL = "claude-sonnet-5-5"
DEFAULT_JUDGMENT_MODEL = "claude-haiku-5-5"


@dataclass(frozen=True)
class ResolvedModels:
    synthesis: str
    judgment: str
    source: Literal["api", "defaults"]


def _newest(models: list[Any], family: str) -> str | None:
    matches = [m for m in models if family in str(getattr(m, "id", "")).lower()]
    if not matches:
        return None
    matches.sort(key=lambda m: getattr(m, "created_at", None) or "", reverse=True)
    return str(matches[0].id)


def resolve_model_ids() -> ResolvedModels:
    """Newest Sonnet/Haiku IDs via ``anthropic.models.list()`` when a key is set; else defaults."""
    defaults = ResolvedModels(DEFAULT_SYNTHESIS_MODEL, DEFAULT_JUDGMENT_MODEL, "defaults")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return defaults
    try:
        import anthropic

        client = anthropic.Anthropic()
        models = list(client.models.list(limit=200))
    except Exception as e:  # network, auth, SDK shape — never block `amber init`
        log.warning("could not list models via the API (%s); writing defaults", e)
        return defaults
    synthesis = _newest(models, "sonnet") or DEFAULT_SYNTHESIS_MODEL
    judgment = _newest(models, "haiku") or DEFAULT_JUDGMENT_MODEL
    return ResolvedModels(synthesis, judgment, "api")
