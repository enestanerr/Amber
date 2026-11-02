"""Backend selection: ``AMBER_MODEL_BACKEND`` → ``[models].backend`` → auto
(``ANTHROPIC_API_KEY`` → anthropic; ``claude`` on PATH → cli; else an error with a hint)."""

from __future__ import annotations

import os
import shutil

from amber.config import ModelsCfg
from amber.models.backend import ModelBackend, ModelError
from amber.models.recorded import RecordedBackend

ENV_BACKEND = "AMBER_MODEL_BACKEND"


def resolve_backend(cfg: ModelsCfg | None = None) -> ModelBackend:
    choice = os.environ.get(ENV_BACKEND) or (cfg.backend if cfg else "auto")
    if choice == "recorded":
        return RecordedBackend()
    if choice == "anthropic" or (choice == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        from amber.models.anthropic_api import AnthropicBackend

        return AnthropicBackend()
    if choice == "cli" or (choice == "auto" and shutil.which("claude")):
        from amber.models.claude_cli import ClaudeCliBackend

        return ClaudeCliBackend()
    if choice not in ("auto", "anthropic", "cli", "recorded"):
        raise ModelError(
            f"unknown model backend {choice!r}",
            hint="Use anthropic, cli, recorded or auto (AMBER_MODEL_BACKEND / [models].backend).",
        )
    raise ModelError(
        "no model backend available",
        hint="Set ANTHROPIC_API_KEY, or install and log in the claude CLI.",
    )


def optional_backend(cfg: ModelsCfg | None = None) -> ModelBackend | None:
    """:func:`resolve_backend`, or ``None`` when ``auto`` finds nothing.

    Running a program needs a model only at its judgment steps (the runner stops there with a
    guard event when there is none), and a scheduler with nothing due needs none at all. An
    explicitly configured but unknown backend still raises.
    """
    choice = os.environ.get(ENV_BACKEND) or (cfg.backend if cfg else "auto")
    if choice != "auto":
        return resolve_backend(cfg)
    try:
        return resolve_backend(cfg)
    except ModelError:
        return None
