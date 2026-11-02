"""Canonical JSON.

Two flavours, both ``sort_keys=True`` and ``ensure_ascii=False``:

* :func:`dumps_canonical` — pretty, two-space indent, trailing newline; used for every JSON
  file Amber writes to disk so diffs are stable.
* :func:`dumps_compact` — no whitespace; used for fingerprints and hashes
  (``docs/02-trace-format.md`` §2: floats via ``repr``, ``-0.0 → 0.0``, NaN rejected).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def normalize_floats(obj: Any) -> Any:
    """Return a copy of *obj* with ``-0.0`` normalised to ``0.0`` and NaN/Inf rejected.

    Integral floats stay floats (``1.0`` is not turned into ``1``) so that fingerprints do not
    conflate the two types.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError("NaN and Infinity are not representable in canonical JSON")
        return 0.0 if obj == 0.0 else obj
    if isinstance(obj, dict):
        items: dict[str, Any] = {}
        for k, v in obj.items():  # pyright: ignore[reportUnknownVariableType]
            items[str(k)] = normalize_floats(v)  # pyright: ignore[reportUnknownArgumentType]
        return items
    if isinstance(obj, list | tuple):
        return [normalize_floats(v) for v in obj]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return obj


def dumps_compact(obj: Any) -> str:
    """Compact canonical JSON used for hashing and fingerprints."""
    return json.dumps(
        normalize_floats(obj),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def dumps_canonical(obj: Any) -> str:
    """Pretty canonical JSON for files on disk (two-space indent, newline at EOF)."""
    text = json.dumps(
        normalize_floats(obj),
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    return text + "\n"


def loads(text: str) -> Any:
    """``json.loads`` that rejects NaN/Infinity (they are not canonical)."""

    def _reject(name: str) -> Any:
        raise ValueError(f"non-canonical JSON constant: {name}")

    return json.loads(text, parse_constant=_reject)


def write_json(path: Path, obj: Any) -> None:
    """Write *obj* as canonical JSON (UTF-8, LF) creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_canonical(obj), encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    """Read a JSON file written by :func:`write_json`."""
    return loads(path.read_text(encoding="utf-8"))
