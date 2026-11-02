"""``ModelBackend`` protocol: one structured-output completion (``docs/04-compiler.md`` §D2)."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from amber.errors import AmberError
from amber.trace.models import Usage

T = TypeVar("T", bound=BaseModel)

__all__ = ["ModelBackend", "ModelError", "T", "Usage"]


class ModelError(AmberError):
    exit_code = 1


class ModelBackend(Protocol):
    name: str

    async def complete(
        self, schema: type[T], system: str, user: str, *, model: str, max_tokens: int
    ) -> tuple[T, Usage]:
        """Return an instance of *schema* parsed from the model's structured output."""
        ...
