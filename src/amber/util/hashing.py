"""SHA-256 helpers over canonical JSON."""

from __future__ import annotations

import hashlib
from typing import Any

from amber.util.json import dumps_compact


def sha256_hex(data: bytes | str) -> str:
    """Hex digest of *data* (strings are UTF-8 encoded)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def digest(obj: Any, *, length: int = 16) -> str:
    """Short digest of the compact canonical JSON of *obj* (default 16 hex chars)."""
    return sha256_hex(dumps_compact(obj))[:length]
