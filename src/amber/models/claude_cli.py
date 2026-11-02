"""``cli`` backend: ``claude -p … --output-format json --json-schema <schema>``.

For users without an API key. ``--json-schema`` (``claude --help`` 2.1.316) makes the CLI
validate its answer against the schema and return it in ``structured_output``; we still parse
the result text as a fallback and validate with Pydantic, retrying once.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Any, cast

from pydantic import ValidationError

from amber.models.backend import ModelError, T
from amber.trace.models import Usage

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_candidate(payload: dict[str, Any]) -> Any:
    """``structured_output`` if present, else the first JSON object in the result text."""
    if payload.get("structured_output") is not None:
        return payload["structured_output"]
    text = payload.get("result")
    if isinstance(text, str):
        m = _JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


class ClaudeCliBackend:
    name = "cli"

    def __init__(self, executable: str = "claude") -> None:
        self.executable = executable

    async def _run(self, argv: list[str]) -> dict[str, Any]:
        exe = shutil.which(self.executable)
        if exe is None:
            raise ModelError("claude CLI not found on PATH", hint="Install Claude Code and log in.")
        proc = await asyncio.create_subprocess_exec(
            exe, *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            tail = err.decode("utf-8", "replace").strip()[-400:]
            raise ModelError(f"claude CLI exited {proc.returncode}: {tail}")
        try:
            data = json.loads(out.decode("utf-8", "replace"))
        except ValueError as e:
            raise ModelError("claude CLI returned non-JSON output") from e
        if isinstance(data, list):  # some versions emit the event list
            results = [
                d
                for d in cast(list[Any], data)
                if isinstance(d, dict) and d.get("type") == "result"
            ]
            data = results[-1] if results else {}
        return cast(dict[str, Any], data)

    async def complete(
        self, schema: type[T], system: str, user: str, *, model: str, max_tokens: int
    ) -> tuple[T, Usage]:
        argv = [
            "-p",
            user,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema.model_json_schema()),
            "--system-prompt",
            system,
            "--model",
            model,
            "--strict-mcp-config",
            "--max-turns",
            "1",
        ]
        last_error = ""
        for _attempt in range(2):
            payload = await self._run(argv)
            candidate = extract_candidate(payload)
            usage_raw = cast(dict[str, Any], payload.get("usage") or {})
            usage = Usage(
                input_tokens=int(usage_raw.get("input_tokens") or 0),
                output_tokens=int(usage_raw.get("output_tokens") or 0),
                cache_read_tokens=int(usage_raw.get("cache_read_input_tokens") or 0),
                cache_creation_tokens=int(usage_raw.get("cache_creation_input_tokens") or 0),
            )
            try:
                return schema.model_validate(candidate), usage
            except ValidationError as e:
                last_error = str(e).splitlines()[0]
        raise ModelError(
            f"claude CLI did not return valid structured output ({last_error})",
            hint="Try the anthropic backend (ANTHROPIC_API_KEY) or a stronger model.",
        )
