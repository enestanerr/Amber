"""``cli`` backend: ``claude -p … --output-format json --json-schema <schema>``.

For users without an API key (a claude.ai subscription is enough). ``--json-schema``
(``claude --help`` 2.1.316) makes the CLI validate its answer against the schema and return it
in ``structured_output``; we still parse the result text as a fallback and validate with
Pydantic, retrying once. The call is stripped of built-in tools, dynamic system-prompt sections
and session persistence so that the CLI's own context does not dominate the token count.
``--bare`` is deliberately not used: it skips the claude.ai login as well.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
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


EFFORT_ENV = "AMBER_CLI_EFFORT"
LOW_EFFORT_MAX_TOKENS = 2000


def effort_for(max_tokens: int) -> str | None:
    """``--effort`` for a call. The CLI has no output cap and thinks at length by default (a
    weekly-report judgment produced 9.9k output tokens, 6.4k at ``low`` with the same correct
    counts), so short structured answers (``max_tokens`` <= 2000: judgments) run at ``low``.
    ``AMBER_CLI_EFFORT`` overrides (``default`` leaves the flag out)."""
    env = os.environ.get(EFFORT_ENV, "").strip()
    if env:
        return None if env == "default" else env
    return "low" if max_tokens <= LOW_EFFORT_MAX_TOKENS else None


class ClaudeCliBackend:
    name = "cli"

    def __init__(self, executable: str = "claude") -> None:
        self.executable = executable

    async def _run(self, argv: list[str], stdin: str = "") -> dict[str, Any]:
        """Spawn ``claude`` with the prompt on stdin (argv has a 32 KiB limit on Windows)."""
        exe = shutil.which(self.executable)
        if exe is None:
            raise ModelError("claude CLI not found on PATH", hint="Install Claude Code and log in.")
        proc = await asyncio.create_subprocess_exec(
            exe,
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(stdin.encode("utf-8"))
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
        # the system prompt goes through a file and the user prompt through stdin: synthesis
        # prompts carry whole recordings and would overflow the Windows command line
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", encoding="utf-8", delete=False, prefix="amber-system-"
        ) as f:
            f.write(system)
        system_file = Path(f.name)
        argv = [
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema.model_json_schema()),
            "--system-prompt-file",
            str(system_file),
            "--model",
            model,
            "--strict-mcp-config",
            # a structured answer is delivered through a tool call, so one turn is not enough
            "--max-turns",
            "2",
            # no built-in tools, no CLAUDE.md / env sections, no session file: measured on
            # claude 2.1.316 this takes a judgment call from ~24k to ~1.2k input tokens
            "--tools",
            "",
            "--exclude-dynamic-system-prompt-sections",
            "--no-session-persistence",
        ]
        effort = effort_for(max_tokens)
        if effort:
            argv += ["--effort", effort]
        last_error = ""
        spent = Usage()  # a rejected first answer still cost tokens: report both attempts
        try:
            for _attempt in range(2):
                payload = await self._run(argv, user)
                candidate = extract_candidate(payload)
                usage_raw = cast(dict[str, Any], payload.get("usage") or {})
                cost = payload.get("total_cost_usd")
                spent = spent + Usage(
                    input_tokens=int(usage_raw.get("input_tokens") or 0),
                    output_tokens=int(usage_raw.get("output_tokens") or 0),
                    cache_read_tokens=int(usage_raw.get("cache_read_input_tokens") or 0),
                    cache_creation_tokens=int(usage_raw.get("cache_creation_input_tokens") or 0),
                    cost_usd=float(cost) if isinstance(cost, int | float) else None,
                )
                try:
                    return schema.model_validate(candidate), spent
                except ValidationError as e:
                    last_error = str(e).splitlines()[0]
        finally:
            system_file.unlink(missing_ok=True)
        raise ModelError(
            f"claude CLI did not return valid structured output ({last_error})",
            hint="Try the anthropic backend (ANTHROPIC_API_KEY) or a stronger model.",
        )
