"""Time and identifier helpers. IDs look like ``t_20261102_191500_1a2b3c``."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(tz=UTC)


def iso(dt: datetime) -> str:
    """ISO-8601 with seconds precision and explicit offset."""
    return dt.isoformat(timespec="seconds")


def new_id(prefix: str, *, now: datetime | None = None) -> str:
    """``<prefix>_<yyyymmdd>_<HHMMSS>_<6 hex>``: sortable by creation time, unique locally."""
    stamp = (now or utcnow()).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{secrets.token_hex(3)}"
