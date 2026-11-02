"""Canonicalization: property tests (hypothesis) and normalize_result cases."""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from amber.trace.canonical import (
    VOLATILE,
    args_fingerprint,
    builtin_effect,
    canonical_json,
    canonical_tool_name,
    canonicalize_args,
    normalize_result,
    split_canonical,
)
from amber.util.json import loads

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53), max_value=2**53)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=8), children, max_size=5)
    ),
    max_leaves=25,
)


def _shuffle_keys(obj: Any, rng: random.Random) -> Any:
    if isinstance(obj, dict):
        items = list(obj.items())
        rng.shuffle(items)
        return {k: _shuffle_keys(v, rng) for k, v in items}
    if isinstance(obj, list):
        return [_shuffle_keys(v, rng) for v in obj]
    return obj


@settings(max_examples=200)
@given(json_values)
def test_canonical_json_is_idempotent(value: Any) -> None:
    once = canonical_json(value)
    assert canonical_json(loads(once)) == once


@settings(max_examples=200)
@given(json_values, st.integers(min_value=0, max_value=10_000))
def test_canonical_json_ignores_key_order(value: Any, seed: int) -> None:
    assert canonical_json(_shuffle_keys(value, random.Random(seed))) == canonical_json(value)


@settings(max_examples=100)
@given(st.dictionaries(st.text(min_size=1, max_size=6), json_values, max_size=4))
def test_canonicalize_args_is_idempotent(args: dict[str, Any]) -> None:
    once = canonicalize_args(args)
    assert canonicalize_args(once) == once
    assert args_fingerprint(args) == args_fingerprint(once)


def test_canonical_json_normalizes_negative_zero_and_rejects_nan() -> None:
    assert canonical_json({"x": -0.0}) == '{"x":0.0}'
    assert canonical_json(1.0) == "1.0"
    with pytest.raises(ValueError):
        canonical_json(float("nan"))


def test_canonicalize_args_masks_volatile_paths_and_strips_trailing_ws() -> None:
    args = {
        "since": "2026-10-12",
        "items": [{"id": "a", "v": 1}, {"id": "b", "v": 2}],
        "note": "hi  \n",
    }
    out = canonicalize_args(args, ["$.since", "$.items[*].id", "$.absent"])
    assert out["since"] == VOLATILE
    assert [i["id"] for i in out["items"]] == [VOLATILE, VOLATILE]
    assert [i["v"] for i in out["items"]] == [1, 2]
    assert out["note"] == "hi"
    assert args["since"] == "2026-10-12"  # input untouched


def test_fingerprint_is_order_insensitive_and_sixteen_hex() -> None:
    a = args_fingerprint({"x": 1, "y": [1, 2]})
    b = args_fingerprint({"y": [1, 2], "x": 1})
    assert a == b and len(a) == 16 and int(a, 16) >= 0
    assert args_fingerprint({"x": 2}) != a


def test_tool_names() -> None:
    assert canonical_tool_name("shop", "list_orders") == "shop.list_orders"
    assert split_canonical("shop.list_orders") == ("shop", "list_orders")
    assert split_canonical("builtin.Bash") == ("builtin", "Bash")
    with pytest.raises(ValueError):
        split_canonical("nodot")
    assert builtin_effect("Read") == "read"
    assert builtin_effect("Write") == "write"
    assert builtin_effect("Bash") == "unknown"
    assert builtin_effect("Whatever") == "unknown"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, (None, "empty")),
        ("", (None, "empty")),
        ("plain text", ("plain text", "text")),
        ('[{"id": "o_1", "total": 42.5}]', ([{"id": "o_1", "total": 42.5}], "json")),
        ({"ok": True}, ({"ok": True}, "json")),
        ([1, 2], ([1, 2], "json")),
        (3, (3, "json")),
        ([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], ("a\nb", "text")),
        ([{"type": "text", "text": '{"k": 1}'}], ({"k": 1}, "json")),
        ({"content": [{"type": "text", "text": "x"}], "isError": False}, ("x", "text")),
        ({"content": [], "structuredContent": {"n": 1}}, ({"n": 1}, "json")),
        ({"content": []}, (None, "empty")),
    ],
)
def test_normalize_result_cases(raw: Any, expected: tuple[Any, str]) -> None:
    assert normalize_result(raw) == expected


def test_normalize_result_blobs_go_to_sink() -> None:
    stored: list[tuple[str, int, str | None]] = []
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    import base64

    blocks = [
        {"type": "text", "text": "see image"},
        {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
        {"type": "resource", "resource": {"uri": "file:///tmp/x.txt", "text": "..."}},
    ]
    value, kind = normalize_result(
        blocks, blob_sink=lambda sha, data, mime: stored.append((sha, len(data), mime))
    )
    assert kind == "json"
    assert value[0] == "see image"
    assert value[1]["$blob"]["mime"] == "image/png" and value[1]["$blob"]["bytes"] == len(png)
    assert value[2]["$blob"] == {"type": "resource", "uri": "file:///tmp/x.txt"}
    assert stored and stored[0][0] == value[1]["$blob"]["sha256"] and stored[0][2] == "image/png"


def test_normalize_result_accepts_attribute_objects() -> None:
    class Block:
        type = "text"
        text = '{"a": 1}'

    class Result:
        content = [Block()]
        structured_content = None
        is_error = False

    assert normalize_result(Result()) == ({"a": 1}, "json")
    assert json.loads(canonical_json({"b": 2})) == {"b": 2}


def test_result_envelope_is_dropped_on_every_path() -> None:
    """Claude Code shows the agent ``{"result": [...]}`` as JSON text, the live backend reads
    structured content: both must canonicalize to the bare list (live run stopped on
    ``result_shape: $: type array, recorded object`` before this)."""
    from types import SimpleNamespace

    from amber.trace.canonical import normalize_result

    rows = [{"id": "o_0710", "total": 42.5}]
    as_agent_text = [{"type": "text", "text": '{"result":[{"id":"o_0710","total":42.5}]}'}]
    live = SimpleNamespace(structured_content={"result": rows}, content=[])
    assert normalize_result(as_agent_text) == (rows, "json")
    assert normalize_result(live) == (rows, "json")
    assert normalize_result('{"result": 3}') == (3, "json")
    assert normalize_result({"result": 1, "other": 2}) == ({"result": 1, "other": 2}, "json")
