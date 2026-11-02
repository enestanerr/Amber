from __future__ import annotations

import pytest

from amber.trace.jsonpath import (
    MISSING,
    WILD,
    expand_wildcards,
    format_path,
    get_path,
    iter_leaves,
    parse_path,
    set_path,
)

DOC = {"a": {"b": [1, {"c": "x"}, 3]}, "empty": [], "k.ey": 0}


@pytest.mark.parametrize(
    ("path", "parts"),
    [
        ("$", ()),
        ("$.a", ("a",)),
        ("$.a.b[1].c", ("a", "b", 1, "c")),
        ("$.items[*].id", ("items", WILD, "id")),
        ('$["k.ey"]', ("k.ey",)),
        ("$.a[-1]", ("a", -1)),
    ],
)
def test_parse_and_format_roundtrip(path: str, parts: tuple[object, ...]) -> None:
    assert parse_path(path) == parts
    assert format_path(parse_path(path)) == path


@pytest.mark.parametrize("bad", ["a.b", "$.", "$[x]", "$.a[1", "$!"])
def test_parse_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_path(bad)


def test_iter_leaves_lists_scalars_and_empty_containers() -> None:
    leaves = dict(iter_leaves(DOC))
    assert leaves == {
        "$.a.b[0]": 1,
        "$.a.b[1].c": "x",
        "$.a.b[2]": 3,
        "$.empty": [],
        '$["k.ey"]': 0,
    }


def test_get_path() -> None:
    assert get_path(DOC, "$.a.b[1].c") == "x"
    assert get_path(DOC, "$.a.b[-1]") == 3
    assert get_path(DOC, "$.a.zzz") is MISSING
    assert get_path(DOC, "$.a.b[9]") is MISSING
    assert get_path(DOC, "$") is DOC
    with pytest.raises(ValueError):
        get_path(DOC, "$.a[*]")


def test_expand_wildcards_and_set_path() -> None:
    doc = {"items": [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}], "m": {"x": 1, "y": 2}}
    assert expand_wildcards(doc, "$.items[*].id") == ["$.items[0].id", "$.items[1].id"]
    assert expand_wildcards(doc, "$.m[*]") == ["$.m.x", "$.m.y"]
    assert expand_wildcards(doc, "$.nope[*].id") == []
    assert set_path(doc, "$.items[1].id", "<v>") is True
    assert doc["items"][1]["id"] == "<v>"
    assert set_path(doc, "$.items[5].id", 0) is False
    assert set_path(doc, "$.m.z", 0) is False  # absent keys are not created
    with pytest.raises(ValueError):
        set_path(doc, "$.items[*]", 0)
