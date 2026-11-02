"""Runtime judgment calls (``docs/05-runtime.md`` §2.3) and live-LLM agreement checks
(``docs/04-compiler.md`` §E2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from amber.compile.holes import shrink_to_bytes
from amber.compile.prompts import JUDGMENT_SYSTEM
from amber.models.backend import ModelBackend
from amber.trace.canonical import canonical_json
from amber.trace.models import Usage

JUDGMENT_MAX_TOKENS = 1200
CONTEXT_BYTES = 40 * 1024


def fit_context(context: dict[str, Any], limit: int = CONTEXT_BYTES) -> dict[str, Any]:
    """Whole values while the context fits in *limit* bytes of compact JSON; beyond that the
    largest value is shrunk (lists cut with a ``...+N`` marker, long strings cut) until it fits.

    A judgment often aggregates over a whole result (a weekly summary over all 75 orders and
    both 120-row RFM snapshots); cutting every list to 20 items made it report wrong numbers.
    """
    out = dict(context)
    sizes = {k: len(canonical_json(v)) for k, v in out.items()}
    while sizes and sum(sizes.values()) > limit:
        key = max(sizes, key=lambda k: sizes[k])
        shrunk = shrink_to_bytes(context[key], limit=max(1024, sizes[key] // 2))
        new_size = len(canonical_json(shrunk))
        if new_size >= sizes[key]:
            break  # nothing left to cut
        out[key] = shrunk
        sizes[key] = new_size
    return out


def render_user(step_id: str, prompt: str, context: dict[str, Any]) -> str:
    """Prompt followed by the context as compact JSON (see :func:`fit_context`); carries
    ``step_id`` for the recorded backend."""
    payload = {"step_id": step_id, "context": fit_context(context)}
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prompt}\n\nContext (JSON):\n{rendered}"


async def judge[T: BaseModel](
    backend: ModelBackend,
    step_id: str,
    schema: type[T],
    prompt: str,
    context: dict[str, Any],
    *,
    model: str,
) -> tuple[T, Usage]:
    return await backend.complete(
        schema,
        JUDGMENT_SYSTEM,
        render_user(step_id, prompt, context),
        model=model,
        max_tokens=JUDGMENT_MAX_TOKENS,
    )


class JudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_meaning: bool = Field(description="Whether the two texts say the same thing.")
    notes: str = Field(description="One sentence on the difference, if any.")


@dataclass
class Agreement:
    step_id: str
    field: str
    recorded: Any
    produced: Any
    agree: bool
    method: str  # exact | judge
    notes: str = ""


async def compare_outputs(
    backend: ModelBackend,
    step_id: str,
    recorded: dict[str, Any],
    produced: dict[str, Any],
    *,
    model: str,
) -> tuple[list[Agreement], Usage]:
    """Exact match for enums/bools/numbers; a ``JudgeOut`` call for free text."""
    out: list[Agreement] = []
    usage = Usage()
    for name, rec in recorded.items():
        if name == "confidence":
            continue
        prod = produced.get(name)
        if isinstance(rec, str) and len(rec) >= 24 and isinstance(prod, str):
            user = json.dumps({"step_id": f"judge:{step_id}", "recorded": rec, "produced": prod})
            verdict, used = await backend.complete(
                JudgeOut,
                "Compare two texts; answer only with the fields.",
                user,
                model=model,
                max_tokens=200,
            )
            usage = usage + used
            out.append(
                Agreement(step_id, name, rec, prod, verdict.same_meaning, "judge", verdict.notes)
            )
        else:
            out.append(Agreement(step_id, name, rec, prod, rec == prod, "exact"))
    return out, usage
