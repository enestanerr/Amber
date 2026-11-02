"""``anthropic`` backend: Messages API structured outputs via ``messages.parse``."""

from __future__ import annotations

from typing import Any

from amber.models.backend import ModelError, T
from amber.trace.models import Usage


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def complete(
        self, schema: type[T], system: str, user: str, *, model: str, max_tokens: int
    ) -> tuple[T, Usage]:
        try:
            response = await self.client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except Exception as e:  # SDK errors become one-line AmberErrors
            raise ModelError(f"anthropic API call failed: {type(e).__name__}: {e}") from e
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ModelError(
                "the model returned no structured output",
                hint="Retry; if it persists, lower max_tokens pressure or simplify the hole.",
            )
        usage_obj = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage_obj, "cache_read_input_tokens", 0) or 0),
            cache_creation_tokens=int(getattr(usage_obj, "cache_creation_input_tokens", 0) or 0),
        )
        return schema.model_validate(parsed, from_attributes=True), usage
