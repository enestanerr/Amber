"""Example servers: generators deterministic by seed; list_tools schemas golden."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
SHARED = REPO / "examples" / "_shared"
GOLDEN = Path(__file__).parent / "golden"


def _load(path: Path, name: str) -> Any:
    sys.path.insert(0, str(SHARED))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need the module registered while it executes
    spec.loader.exec_module(module)
    return module


def test_synth_is_deterministic() -> None:
    synth = _load(SHARED / "synth.py", "synth_mod")
    assert (
        len(synth.orders()) == 900 and len(synth.customers()) == 120 and len(synth.PRODUCTS) == 14
    )
    assert synth.digest() == synth.digest() and synth.digest(seed=8) != synth.digest()
    all_orders = synth.orders()
    assert len({o.id for o in all_orders}) == 900  # ids unique; list is sorted by created_at
    assert all_orders[0].created_at <= all_orders[-1].created_at
    assert all_orders[0].created_at.endswith("Z") and all_orders[0].total > 0
    assert synth.week_range("2026-W44") == (synth.date(2026, 10, 26), synth.date(2026, 11, 1))


async def _tools(module: Any) -> list[dict[str, Any]]:
    from mcp import Client

    async with Client(module.mcp) as client:
        listing = await client.list_tools()
    return sorted(
        (
            {
                "name": t.name,
                "required": list((t.input_schema or {}).get("required", [])),
                "properties": sorted((t.input_schema or {}).get("properties", {})),
                "read_only": bool(t.annotations and t.annotations.read_only_hint),
            }
            for t in listing.tools
        ),
        key=lambda d: d["name"],
    )


@pytest.mark.parametrize(
    ("script", "name"),
    [
        (REPO / "examples" / "weekly-report" / "server_shop.py", "shop"),
        (SHARED / "server_slack.py", "slack"),
    ],
)
async def test_list_tools_golden(
    script: Path, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    module = _load(script, f"{name}_server_mod")
    tools = await _tools(module)
    golden = GOLDEN / f"{name}_tools.json"
    if not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(tools, indent=2) + "\n", encoding="utf-8")
    assert tools == json.loads(golden.read_text(encoding="utf-8"))


async def test_shop_tools_and_scenario_b(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mcp import Client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMBER_SCENARIO", "A")
    shop = _load(REPO / "examples" / "weekly-report" / "server_shop.py", "shop_a")
    async with Client(shop.mcp) as client:
        wr = (await client.call_tool("week_range", {"week": "2026-W44"})).structured_content
        assert wr == {"start": "2026-10-26", "end": "2026-11-01"}
        orders = (
            await client.call_tool("list_orders", {"since": "2026-10-26", "until": "2026-11-01"})
        ).structured_content
        rows = orders["result"] if isinstance(orders, dict) and "result" in orders else orders
        assert len(rows) == 75 and {"id", "customer_id", "created_at", "total", "items"} <= set(
            rows[0]
        )
        top = (
            await client.call_tool(
                "top_products", {"since": "2026-10-26", "until": "2026-11-01", "n": 3}
            )
        ).structured_content
        top_rows = top["result"] if isinstance(top, dict) and "result" in top else top
        assert len(top_rows) == 3 and top_rows[0]["revenue"] >= top_rows[1]["revenue"]
        rfm = (await client.call_tool("rfm_snapshot", {"as_of": "2026-10-25"})).structured_content
        rfm_rows = rfm["result"] if isinstance(rfm, dict) and "result" in rfm else rfm
        assert rfm_rows and {"customer_id", "r", "f", "m"} == set(rfm_rows[0])
    monkeypatch.setenv("AMBER_SCENARIO", "B")
    shop_b = _load(REPO / "examples" / "weekly-report" / "server_shop.py", "shop_b")
    async with Client(shop_b.mcp) as client:
        orders = (
            await client.call_tool("list_orders", {"since": "2026-10-26", "until": "2026-11-01"})
        ).structured_content
        rows = orders["result"] if isinstance(orders, dict) and "result" in orders else orders
        assert "amount" in rows[0] and "total" not in rows[0]


async def test_slack_outbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from mcp import Client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMBER_OUTBOX", str(tmp_path / "out" / "slack.jsonl"))
    slack = _load(SHARED / "server_slack.py", "slack_mod")
    async with Client(slack.mcp) as client:
        res = (
            await client.call_tool("post_message", {"channel": "#eng", "text": "hello"})
        ).structured_content
        assert res["ok"] is True and res["ts"]
        channels = (await client.call_tool("list_channels", {})).structured_content
        names = [
            c["name"]
            for c in (
                channels["result"]
                if isinstance(channels, dict) and "result" in channels
                else channels
            )
        ]
        assert "eng" in names
    line = json.loads(
        (tmp_path / "out" / "slack.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert line["channel"] == "#eng" and line["text"] == "hello"
