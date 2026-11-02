"""Parsing of repeatable ``--input k=v`` and ``--status`` options."""

from __future__ import annotations

from typing import cast

from amber.errors import UsageError
from amber.trace.models import Status


def parse_inputs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        key, eq, value = pair.partition("=")
        if not eq or not key.strip():
            raise UsageError(f"bad --input {pair!r}", hint="Use --input key=value (repeatable).")
        out[key.strip()] = value
    return out


def parse_status(value: str | None) -> Status | None:
    if value is None:
        return None
    if value not in ("success", "failure", "unknown"):
        raise UsageError(f"bad --status {value!r}", hint="Use success, failure or unknown.")
    return cast(Status, value)
