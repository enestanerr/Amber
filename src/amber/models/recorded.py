"""``recorded`` backend: serves fills from JSON fixtures so compiles and tests stay offline.

Fixture files (``tests/fixtures/model_fills/*.json``, or ``AMBER_MODEL_FILLS``) map an id to one
fill or a list of fills (one per repair attempt)::

    {
        "transform_step3": [{"python_source": "...", "rationale": "..."}],
        "judgment_step4": {"prompt": "...", "fields": [...], "rationale": "..."},
    }

The id is read from the JSON user message (``hole_id`` for synthesis, ``step_id`` at runtime).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, cast

from amber.models.backend import ModelError, T
from amber.trace.models import Usage
from amber.util.json import read_json

ENV_FILLS = "AMBER_MODEL_FILLS"
_ID_RE = re.compile(r'"(?:hole_id|step_id)":\s*"([^"]+)"')


def default_fills_dir() -> Path:
    env = os.environ.get(ENV_FILLS)
    if env:
        return Path(env)
    local = Path.cwd() / "tests" / "fixtures" / "model_fills"
    if local.is_dir():
        return local
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "model_fills"


def load_fills(directory: Path) -> dict[str, list[dict[str, Any]]]:
    fills: dict[str, list[dict[str, Any]]] = {}
    if not directory.is_dir():
        return fills
    for file in sorted(directory.glob("*.json")):
        data = cast(dict[str, Any], read_json(file))
        for key, value in data.items():
            items = value if isinstance(value, list) else [value]
            fills.setdefault(key, []).extend(cast(list[dict[str, Any]], items))
    return fills


class RecordedBackend:
    name = "recorded"

    def __init__(
        self,
        fills: dict[str, list[dict[str, Any]]] | None = None,
        *,
        directory: Path | None = None,
    ) -> None:
        self.directory = directory
        self.fills = (
            dict(fills) if fills is not None else load_fills(directory or default_fills_dir())
        )
        self.calls: list[tuple[str, int]] = []

    @staticmethod
    def _key(user: str) -> tuple[str, int]:
        try:
            obj = json.loads(user)
        except ValueError:
            # rendered judgment prompts end with a JSON block that carries the step id
            m = _ID_RE.search(user)
            return (m.group(1) if m else user.strip()[:80]), 0
        if isinstance(obj, dict):
            d = cast(dict[str, Any], obj)
            key = d.get("hole_id") or d.get("step_id") or ""
            return str(key), int(d.get("attempt") or 0)
        return "", 0

    async def complete(
        self, schema: type[T], system: str, user: str, *, model: str, max_tokens: int
    ) -> tuple[T, Usage]:
        key, attempt = self._key(user)
        self.calls.append((key, attempt))
        options = self.fills.get(key)
        if not options:
            raise ModelError(
                f"no recorded model fill for {key!r}",
                hint=f"Add it to a JSON file in {self.directory or default_fills_dir()} "
                f"(or unset {ENV_FILLS} / AMBER_MODEL_BACKEND to use a real model).",
            )
        fill = options[min(attempt, len(options) - 1)]
        usage = Usage(input_tokens=len(user) // 4, output_tokens=len(json.dumps(fill)) // 4)
        return schema.model_validate(fill), usage
