"""issue-triage server (goldens, scenario B rejects old labels) and price-watch site generation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "examples" / "_shared"
TRIAGE = REPO / "examples" / "issue-triage"
WATCH = REPO / "examples" / "price-watch"
GOLDEN = Path(__file__).parent / "golden"


def _load(path: Path, name: str, *extra: Path) -> Any:
    for p in (SHARED, *extra):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _rows(result: Any) -> Any:
    sc = result.structured_content
    return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc


def test_issue_data_is_deterministic() -> None:
    scenario = _load(TRIAGE / "scenario.py", "triage_scenario", TRIAGE)
    data = scenario.issues()
    assert len(data) == 40 and {i["area"] for i in data} == set(scenario.AREAS)
    assert data == scenario.issues()
    for original, dup in scenario.DUPLICATES:
        assert data[dup - 1]["title"].startswith(data[original - 1]["title"])
        # "duplicates an older one" must hold in the data the agent sees
        assert data[original - 1]["created_at"] < data[dup - 1]["created_at"]
    assert len({i["created_at"] for i in data}) >= 5


async def test_github_tools_golden_and_scenario_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp import Client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMBER_SCENARIO", "A")
    gh = _load(TRIAGE / "server_github.py", "github_a", TRIAGE)
    async with Client(gh.mcp) as client:
        listing = await client.list_tools()
        tools = sorted(
            (
                {
                    "name": t.name,
                    "required": list((t.input_schema or {}).get("required", [])),
                    "read_only": bool(t.annotations and t.annotations.read_only_hint),
                }
                for t in listing.tools
            ),
            key=lambda d: d["name"],
        )
        golden = GOLDEN / "github_tools.json"
        if not golden.exists():
            golden.write_text(json.dumps(tools, indent=2) + "\n", encoding="utf-8", newline="\n")
        assert tools == json.loads(golden.read_text(encoding="utf-8"))
        new = _rows(await client.call_tool("list_issues", {"since": "2026-10-28"}))
        assert new and all(i["created_at"] >= "2026-10-28" for i in new)
        hit = _rows(await client.call_tool("search_issues", {"query": "dark mode"}))
        assert hit and "Dark mode" in hit[0]["title"]
        batch = [{"number": 1, "labels": ["area:ui"]}, {"number": 2, "labels": ["area:api"]}]
        ok = _rows(await client.call_tool("add_labels", {"updates": batch}))
        assert ok == {"ok": True, "updated": [1, 2]}
        comment = _rows(
            await client.call_tool(
                "add_comments", {"comments": [{"number": 1, "body": "Duplicate of #3"}]}
            )
        )
        assert comment["ok"] and comment["ids"][0] >= 1001
        issue = _rows(await client.call_tool("get_issue", {"number": 1}))
        assert issue["comments"][0]["body"] == "Duplicate of #3" and "area:ui" in issue["labels"]
        # all or nothing: one unknown label rejects the whole batch
        mixed = [{"number": 3, "labels": ["area:docs"]}, {"number": 4, "labels": ["area:nope"]}]
        bad = await client.call_tool("add_labels", {"updates": mixed})
        assert bad.is_error
        assert _rows(await client.call_tool("get_issue", {"number": 3}))["labels"] == []
    monkeypatch.setenv("AMBER_SCENARIO", "B")
    gh_b = _load(TRIAGE / "server_github.py", "github_b", TRIAGE)
    async with Client(gh_b.mcp) as client:
        rejected = await client.call_tool(
            "add_labels", {"updates": [{"number": 2, "labels": ["area:ui"]}]}
        )
        assert rejected.is_error
        accepted = _rows(
            await client.call_tool(
                "add_labels", {"updates": [{"number": 2, "labels": ["kind/ui"]}]}
            )
        )
        assert accepted["ok"] is True


def test_price_watch_site_scenarios(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = _load(WATCH / "scenario.py", "watch_scenario", WATCH)
    monkeypatch.setattr(scenario, "SITE", tmp_path / "site")
    a = scenario.write_site("A").read_text(encoding="utf-8")
    assert a.count('class="product"') == 6 and 'class="price"' in a and 'class="cost"' not in a
    assert "$48.00" in a and "out of stock" in a
    previous = json.loads((tmp_path / "site" / "previous.json").read_text(encoding="utf-8"))
    assert previous["lantern-brass"] == {"price": 48.0, "stock": 12}
    b = scenario.write_site("B").read_text(encoding="utf-8")
    assert 'class="cost"' in b and 'class="price"' not in b and "$52.00" in b
    changed = [p["sku"] for p in scenario.catalog("B") if p["price"] != previous[p["sku"]]["price"]]
    assert changed == ["lantern-brass", "lamp-storm", "stand-oak"]
