"""A tiny JSON-path subset: ``$.a.b``, ``$.a[0]``, ``$.a[*]`` (``docs/02-trace-format.md`` §2).

No external dependency. ``[*]`` only appears in patterns (volatile paths); concrete paths
produced by :func:`iter_leaves` use explicit indices. Keys that contain ``.`` or ``[`` are
written as ``["key"]``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final, cast


@dataclass(frozen=True)
class Wildcard:
    def __repr__(self) -> str:
        return "*"


WILD: Final = Wildcard()
PathPart = str | int | Wildcard


class Missing:
    """Sentinel for "path not present" (distinct from ``None``, which is a JSON value)."""

    def __repr__(self) -> str:
        return "<missing>"


MISSING: Final = Missing()

_SIMPLE_KEY_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def parse_path(path: str) -> tuple[PathPart, ...]:
    """``"$.a[0].b"`` → ``("a", 0, "b")``; raises ``ValueError`` on malformed input."""
    if not path.startswith("$"):
        raise ValueError(f"json path must start with '$': {path!r}")
    rest = path[1:]
    parts: list[PathPart] = []
    i = 0
    while i < len(rest):
        c = rest[i]
        if c == ".":
            j = i + 1
            while j < len(rest) and rest[j] not in ".[":
                j += 1
            key = rest[i + 1 : j]
            if not key:
                raise ValueError(f"empty key in json path {path!r}")
            parts.append(key)
            i = j
        elif c == "[":
            j = rest.find("]", i)
            if j < 0:
                raise ValueError(f"unclosed '[' in json path {path!r}")
            token = rest[i + 1 : j]
            if token == "*":
                parts.append(WILD)
            elif token.lstrip("-").isdigit():
                parts.append(int(token))
            elif len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
                parts.append(token[1:-1])
            else:
                raise ValueError(f"bad index {token!r} in json path {path!r}")
            i = j + 1
        else:
            raise ValueError(f"unexpected {c!r} at {i} in json path {path!r}")
    return tuple(parts)


def format_path(parts: tuple[PathPart, ...] | list[PathPart]) -> str:
    """Inverse of :func:`parse_path`."""
    out = "$"
    for p in parts:
        if isinstance(p, Wildcard):
            out += "[*]"
        elif isinstance(p, int):
            out += f"[{p}]"
        elif p and all(ch in _SIMPLE_KEY_CHARS for ch in p):
            out += f".{p}"
        else:
            out += f'["{p}"]'
    return out


def iter_leaves(obj: Any, prefix: str = "$") -> Iterator[tuple[str, Any]]:
    """Yield ``(path, value)`` for every scalar leaf (and for empty containers)."""
    if isinstance(obj, dict):
        items: dict[str, Any] = obj  # pyright: ignore[reportUnknownVariableType]
        if not items:
            yield prefix, obj
            return
        for key, value in items.items():
            yield from iter_leaves(value, format_path((*parse_path(prefix), key)))
    elif isinstance(obj, list):
        elems: list[Any] = obj  # pyright: ignore[reportUnknownVariableType]
        if not elems:
            yield prefix, obj
            return
        for i, value in enumerate(elems):
            yield from iter_leaves(value, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def get_path(obj: Any, path: str | tuple[PathPart, ...]) -> Any:
    """Value at *path* or :data:`MISSING`. Wildcards are not allowed here."""
    parts = parse_path(path) if isinstance(path, str) else path
    cur: Any = obj
    for p in parts:
        if isinstance(p, Wildcard):
            raise ValueError("get_path does not accept wildcards; use expand_wildcards")
        if isinstance(p, int):
            if not isinstance(cur, list):
                return MISSING
            lst = cast(list[Any], cur)
            if not -len(lst) <= p < len(lst):
                return MISSING
            cur = lst[p]
        else:
            if not isinstance(cur, dict):
                return MISSING
            d = cast(dict[str, Any], cur)
            if p not in d:
                return MISSING
            cur = d[p]
    return cur


def expand_wildcards(obj: Any, path: str | tuple[PathPart, ...]) -> list[str]:
    """Concrete paths present in *obj* that match a pattern with ``[*]``."""
    parts = parse_path(path) if isinstance(path, str) else path
    found: list[str] = []

    def walk(cur: Any, i: int, acc: tuple[PathPart, ...]) -> None:
        if i == len(parts):
            found.append(format_path(acc))
            return
        p = parts[i]
        if isinstance(p, Wildcard):
            if isinstance(cur, list):
                for k, v in enumerate(cur):  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
                    walk(v, i + 1, (*acc, k))
            elif isinstance(cur, dict):
                for k, v in cur.items():  # pyright: ignore[reportUnknownVariableType]
                    walk(v, i + 1, (*acc, str(k)))  # pyright: ignore[reportUnknownArgumentType]
            return
        nxt = get_path(cur, (p,))
        if nxt is not MISSING:
            walk(nxt, i + 1, (*acc, p))

    walk(obj, 0, ())
    return found


def set_path(obj: Any, path: str | tuple[PathPart, ...], value: Any) -> bool:
    """Set *value* at a concrete *path* in place; returns ``False`` when the path is absent."""
    parts = parse_path(path) if isinstance(path, str) else path
    if not parts:
        return False
    parent = get_path(obj, parts[:-1])
    last = parts[-1]
    if isinstance(last, Wildcard):
        raise ValueError("set_path needs a concrete path; expand wildcards first")
    if isinstance(last, int):
        if isinstance(parent, list) and -len(parent) <= last < len(parent):  # pyright: ignore[reportUnknownArgumentType]
            parent[last] = value
            return True
        return False
    if isinstance(parent, dict) and last in parent:
        parent[last] = value
        return True
    return False
