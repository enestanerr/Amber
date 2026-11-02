"""``amber.toml`` — project configuration (``docs/01-architecture.md`` §Storage).

Loaded with :mod:`tomllib`; validated by Pydantic with ``extra="forbid"`` so typos fail loudly.
Secrets never belong here: :func:`lint_secrets` rejects values that look like keys.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from amber.errors import ConfigError
from amber.util.paths import CONFIG_FILENAME, find_project_root


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCfg(_Strict):
    name: str = "amber-project"


class ModelsCfg(_Strict):
    backend: Literal["auto", "anthropic", "cli", "recorded"] = "auto"
    synthesis: str = "claude-sonnet-5-5"
    judgment: str = "claude-haiku-5-5"


class ServerCfg(_Strict):
    """One MCP server: stdio (``command`` + ``args``) or streamable HTTP (``url``)."""

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None

    @property
    def transport(self) -> Literal["stdio", "http"]:
        return "http" if self.url else "stdio"


class BudgetsCfg(_Strict):
    max_calls_factor: float = 2.0
    # measured: one judgment over ~25 KB of JSON through the claude CLI is ~27k tokens
    max_llm_tokens: int = 40_000
    wall_clock_s: int = 600


class GuardsCfg(_Strict):
    min_confidence: float = 0.5


class FallbackCfg(_Strict):
    daily_budget: int = 3


class BuiltinsCfg(_Strict):
    read_roots: list[str] = Field(default_factory=list)
    bash_env: dict[str, str] = Field(default_factory=dict)


class AmberConfig(_Strict):
    project: ProjectCfg = Field(default_factory=ProjectCfg)
    models: ModelsCfg = Field(default_factory=ModelsCfg)
    servers: dict[str, ServerCfg] = Field(default_factory=dict)
    budgets: BudgetsCfg = Field(default_factory=BudgetsCfg)
    guards: GuardsCfg = Field(default_factory=GuardsCfg)
    fallback: FallbackCfg = Field(default_factory=FallbackCfg)
    builtins: BuiltinsCfg = Field(default_factory=BuiltinsCfg)


# Patterns that look like credentials. Deliberately broad: a false positive costs a
# rename, a false negative leaks a key into a committed file.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "key-like assignment",
        re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*=\s*\"[^\"\s]{16,}\""),
    ),
)


def lint_secrets(text: str) -> list[str]:
    """Return human-readable findings for values in *text* that look like secrets."""
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.split("#", 1)[0]
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(stripped):
                findings.append(f"line {lineno}: looks like a {label}")
                break
    return findings


def parse_config(text: str, *, source: str = CONFIG_FILENAME) -> AmberConfig:
    """Parse and validate TOML text; raises :class:`ConfigError` with a hint."""
    findings = lint_secrets(text)
    if findings:
        raise ConfigError(
            f"{source} contains values that look like secrets ({'; '.join(findings)})",
            hint="Move credentials to environment variables; amber.toml is meant to be committed.",
        )
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{source} is not valid TOML: {e}") from e
    try:
        return AmberConfig.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "<root>"
        raise ConfigError(
            f"{source}: invalid value at [{loc}]: {first['msg']}",
            hint="See docs/01-architecture.md for the amber.toml reference.",
        ) from e


def load_config(root: Path) -> AmberConfig:
    """Load ``<root>/amber.toml``."""
    path = root / CONFIG_FILENAME
    if not path.is_file():
        raise ConfigError(f"no {CONFIG_FILENAME} in {root}", hint="Run `amber init` first.")
    return parse_config(path.read_text(encoding="utf-8"), source=str(path))


def require_project(explicit: Path | None = None) -> tuple[Path, AmberConfig]:
    """Locate the project root and load its config, or fail with the ``amber init`` hint."""
    root = find_project_root(explicit=explicit)
    if root is None:
        where = explicit or Path.cwd()
        raise ConfigError(
            f"no {CONFIG_FILENAME} found in {where} or its parents",
            hint="Run `amber init` in your project directory (or pass --project PATH).",
        )
    return root, load_config(root)
