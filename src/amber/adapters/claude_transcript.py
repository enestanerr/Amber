"""``claude-transcript`` adapter: import ``~/.claude/projects/<encoded cwd>/<session>.jsonl``
(``docs/02-trace-format.md`` §3 A2).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from amber.adapters.claude_messages import ClaudeMessageMapper
from amber.errors import AmberError
from amber.trace.models import Status, Trace
from amber.util.json import loads
from amber.util.time import new_id, utcnow


def encode_cwd(path: str | Path) -> str:
    """Claude Code project folder name: every ``/``, backslash and ``:`` becomes ``-``.

    ``/Users/me/proj`` → ``-Users-me-proj``; ``C:/x/y`` (or with backslashes) → ``C--x-y``.
    """
    return re.sub(r"[\\/:]", "-", str(path))


def projects_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "projects"


def latest_transcript(cwd: Path, *, home: Path | None = None) -> Path:
    """Newest non-agent ``*.jsonl`` for *cwd*."""
    folder = projects_dir(home) / encode_cwd(cwd)
    candidates = [p for p in folder.glob("*.jsonl") if not p.name.startswith("agent-")]
    if not candidates:
        raise AmberError(
            f"no Claude Code transcripts for {cwd} in {folder}",
            hint="Run a Claude Code session in this directory first, or pass a .jsonl path.",
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def read_lines(path: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            obj = loads(raw)
            if isinstance(obj, dict):
                lines.append(cast(dict[str, Any], obj))
    return lines


def own_session_id(lines: list[dict[str, Any]], path: Path) -> str | None:
    """The file's own session: its stem when that id occurs, else the first root line's id."""
    ids = {str(line["sessionId"]) for line in lines if line.get("sessionId")}
    if path.stem in ids:
        return path.stem
    for line in lines:
        if line.get("parentUuid") is None and line.get("sessionId"):
            return str(line["sessionId"])
    return next(iter(ids), None)


def chain(lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Follow ``parentUuid`` links root → leaf; on branches take the longest chain and warn."""
    by_uuid = {str(line["uuid"]): line for line in lines if line.get("uuid")}
    parents = {str(line["parentUuid"]) for line in lines if line.get("parentUuid")}
    leaves = [u for u in by_uuid if u not in parents]
    warnings: list[str] = []
    best: list[dict[str, Any]] = []
    for leaf in leaves:
        path: list[dict[str, Any]] = []
        cur: str | None = leaf
        seen: set[str] = set()
        while cur is not None and cur in by_uuid and cur not in seen:
            seen.add(cur)
            path.append(by_uuid[cur])
            parent = by_uuid[cur].get("parentUuid")
            cur = str(parent) if parent else None
        path.reverse()
        if len(path) > len(best):
            best = path
    if len(leaves) > 1:
        warnings.append(f"transcript has {len(leaves)} branches; took the longest chain")
    return best, warnings


def _timestamp(line: dict[str, Any]) -> datetime | None:
    ts = line.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def import_transcript(
    path: Path,
    *,
    workflow: str,
    prompt: str = "",
    inputs: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    status: Status | None = None,
    trace_id: str | None = None,
) -> tuple[Trace, list[str]]:
    """Build a trace from one transcript file; returns ``(trace, warnings)``."""
    if not path.is_file():
        raise AmberError(f"transcript not found: {path}")
    lines = read_lines(path)
    sid = own_session_id(lines, path)
    kept = [
        line
        for line in lines
        if (sid is None or line.get("sessionId") == sid)
        and not line.get("isSidechain")
        and not line.get("isMeta")
    ]
    ordered, warnings = chain(kept)
    mapper = ClaudeMessageMapper(
        workflow=workflow,
        adapter="claude-transcript",
        task_prompt=prompt,
        inputs=inputs,
        tags=tags,
        origin="import",
    )
    mapper.session_id = sid
    first_ts: datetime | None = None
    for line in ordered:
        ts = _timestamp(line)
        first_ts = first_ts or ts
        if line.get("version") and "version" not in mapper.harness:
            mapper.harness["version"] = str(line["version"])
        kind = line.get("type")
        message = line.get("message")
        msg = cast(dict[str, Any], message) if isinstance(message, dict) else {}
        if kind == "assistant":
            mapper.feed_assistant(msg, parent_tool_use_id=line.get("parentToolUseId"), timestamp=ts)
        elif kind == "user":
            mapper.feed_user(msg, timestamp=ts)
        elif kind == "system" and line.get("subtype") == "compact_boundary":
            mapper.note("system", "compaction")
    for w in warnings:
        mapper.note("adapter", w)
    trace = mapper.build(
        trace_id=trace_id or new_id("t"), created_at=first_ts or utcnow(), status_override=status
    )
    return trace, warnings
