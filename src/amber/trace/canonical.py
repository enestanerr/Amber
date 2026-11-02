"""Canonicalization (``docs/02-trace-format.md`` §2).

* tool names: ``canonical_tool_name(server, tool) == "server.tool"``; builtins use the server
  name ``builtin`` and a fixed effect table;
* ``canonicalize_args``: deep copy, trailing whitespace stripped from strings, volatile paths
  replaced by the ``<volatile>`` sentinel;
* ``args_fingerprint``: ``sha256(canonical_json(canonicalize_args(args, [])))[:16]``;
* ``normalize_result``: MCP content → JSON | text | empty, binary blocks → ``{"$blob": ...}``.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any, Literal, cast

from amber.trace.jsonpath import expand_wildcards, iter_leaves, set_path
from amber.util.json import dumps_compact

Effect = Literal["read", "write", "unknown"]
ResultKind = Literal["json", "text", "empty"]

VOLATILE = "<volatile>"
BUILTIN_SERVER = "builtin"

# Effects of harness builtins by bare name. Anything absent is "unknown" (treated as write by
# the write-effect guard, which is the conservative choice).
BUILTIN_EFFECTS: dict[str, Effect] = {
    "Read": "read",
    "Glob": "read",
    "Grep": "read",
    "LS": "read",
    "WebFetch": "read",
    "WebSearch": "read",
    "Write": "write",
    "Edit": "write",
    "MultiEdit": "write",
    "NotebookEdit": "write",
    "Bash": "unknown",
    "Task": "unknown",
    "TodoWrite": "unknown",
}

BlobSink = Callable[[str, bytes, str | None], None]
"""``sink(sha256, data, mime)`` — stores binary content outside the trace."""


def canonical_tool_name(server: str, tool: str) -> str:
    return f"{server}.{tool}"


def split_canonical(name: str) -> tuple[str, str]:
    """``"shop.list_orders"`` → ``("shop", "list_orders")`` (split on the first dot)."""
    server, _, tool = name.partition(".")
    if not server or not tool:
        raise ValueError(f"not a canonical tool name: {name!r}")
    return server, tool


def builtin_effect(name: str) -> Effect:
    return BUILTIN_EFFECTS.get(name, "unknown")


_SCHEMA_NOISE = frozenset({"title", "description", "$schema", "examples"})


def normalize_schema(schema: Any) -> Any:
    """Strip presentation-only keys (``title``, ``description``, ``$schema``, ``examples``) and
    empty ``required`` lists so two servers describing the same inputs hash the same."""
    if isinstance(schema, dict):
        d = cast(dict[str, Any], schema)
        out: dict[str, Any] = {}
        for key, value in d.items():
            if key in _SCHEMA_NOISE or (key == "required" and value == []):
                continue
            out[key] = normalize_schema(value)
        return out
    if isinstance(schema, list):
        return [normalize_schema(v) for v in cast(list[Any], schema)]
    return schema


def canonical_json(obj: Any) -> str:
    """Compact canonical JSON (sorted keys, no whitespace, ``-0.0 → 0.0``, NaN rejected)."""
    return dumps_compact(obj)


def _strip_strings(obj: Any) -> Any:
    if isinstance(obj, str):
        return obj.rstrip()
    if isinstance(obj, dict):
        d = cast(dict[str, Any], obj)
        return {k: _strip_strings(v) for k, v in d.items()}
    if isinstance(obj, list):
        lst = cast(list[Any], obj)
        return [_strip_strings(v) for v in lst]
    return obj


def canonicalize_args(args: dict[str, Any], volatile_paths: Iterable[str] = ()) -> dict[str, Any]:
    """Deep copy with trailing whitespace stripped and volatile paths masked."""
    out = cast(dict[str, Any], _strip_strings(copy.deepcopy(args)))
    for pattern in volatile_paths:
        for concrete in expand_wildcards(out, pattern):
            set_path(out, concrete, VOLATILE)
    return out


def args_fingerprint(args: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(canonicalize_args(args)).encode("utf-8")).hexdigest()[:16]


def leaf_paths(obj: Any) -> list[str]:
    """Concrete leaf paths of *obj* (helper for provenance and volatility detection)."""
    return [p for p, _ in iter_leaves(obj)]


def _block_field(block: Any, *names: str) -> Any:
    """Read the first present field from a dict or attribute-style content block."""
    if isinstance(block, dict):
        d = cast(dict[str, Any], block)
        for n in names:
            if n in d:
                return d[n]
        return None
    for n in names:
        if hasattr(block, n):
            return getattr(block, n)
    return None


def _blob_entry(block: Any, blob_sink: BlobSink | None) -> dict[str, Any]:
    btype = str(_block_field(block, "type") or "blob")
    if btype in ("resource", "embedded_resource", "resource_link"):
        resource = _block_field(block, "resource")
        uri = _block_field(resource, "uri") if resource is not None else _block_field(block, "uri")
        return {"$blob": {"type": btype, "uri": uri}}
    data = _block_field(block, "data")
    mime = _block_field(block, "mimeType", "mime_type")
    raw = base64.b64decode(data) if isinstance(data, str) else b""
    sha = hashlib.sha256(raw).hexdigest()
    if blob_sink is not None and raw:
        blob_sink(sha, raw, str(mime) if mime else None)
    return {"$blob": {"type": btype, "mime": mime, "sha256": sha, "bytes": len(raw)}}


def _text_or_json(text: str) -> tuple[Any, ResultKind]:
    if text == "":
        return None, "empty"
    try:
        return json.loads(text), "json"
    except ValueError:
        return text, "text"


def normalize_result(result: Any, *, blob_sink: BlobSink | None = None) -> tuple[Any, ResultKind]:
    """Normalize a tool result (MCP ``CallToolResult``, content blocks, plain string, JSON)."""
    if result is None:
        return None, "empty"
    if isinstance(result, str):
        return _text_or_json(result)
    if isinstance(result, bool | int | float):
        return result, "json"

    structured: Any = _block_field(result, "structuredContent", "structured_content")
    if structured is not None:
        # MCP servers wrap non-object return values as {"result": ...}; recordings made through
        # an agent see the plain text form, so unwrap to keep live and recorded shapes equal.
        if isinstance(structured, dict):
            wrapped = cast(dict[str, Any], structured)
            if set(wrapped) == {"result"}:
                return wrapped["result"], "json"
        return cast(Any, structured), "json"

    blocks: list[Any]
    if isinstance(result, list):
        lst = cast(list[Any], result)
        if lst and all(_block_field(b, "type") is not None for b in lst):
            blocks = lst
        else:
            return lst, "json"
    elif isinstance(result, dict):
        d = cast(dict[str, Any], result)
        if "content" in d and isinstance(d["content"], list):
            blocks = cast(list[Any], d["content"])
        else:
            return d, "json"
    else:
        content = _block_field(result, "content")
        if isinstance(content, list):
            blocks = cast(list[Any], content)
        else:
            return result, "json"

    items: list[Any] = []
    for block in blocks:
        btype = _block_field(block, "type")
        if btype == "text":
            items.append(str(_block_field(block, "text") or ""))
        else:
            items.append(_blob_entry(block, blob_sink))
    if not items:
        return None, "empty"
    if all(isinstance(i, str) for i in items):
        return _text_or_json("\n".join(cast(list[str], items)))
    return items, "json"
