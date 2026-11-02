"""Seeded generator for the weekly-report fixtures (synthetic "Northwind Lanterns Co." data).

Writes ``tests/fixtures/traces/weekly-report/{t1,t2,t3,t_broken}.json``. Run:

    uv run python tests/fixtures/gen_weekly_report.py

t2 contains one exploratory ``builtin.Read`` (optional, unreferenced → pruned by align);
``t_broken`` is t1 with steps 2 and 3 swapped plus one extra call (verify must diverge).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from amber.trace.canonical import ResultKind
from amber.trace.models import Outcome, Thought, ToolCall, ToolSchema, Trace, Usage
from amber.util.json import write_json

OUT = Path(__file__).parent / "traces" / "weekly-report"

WEEKS = {
    "2026-W42": ("2026-10-12", "2026-10-18"),
    "2026-W43": ("2026-10-19", "2026-10-25"),
    "2026-W44": ("2026-10-26", "2026-11-01"),
}
PRODUCTS = [
    ("LNT-001", "Brass Hurricane Lantern", 48.0),
    ("LNT-002", "Copper Candle Lantern", 32.5),
    ("LNT-003", "Iron Garden Torch", 27.0),
    ("LNT-004", "Glass Storm Lamp", 54.0),
    ("LNT-005", "Tin Pocket Lantern", 14.0),
    ("LNT-006", "Oak Lantern Stand", 39.0),
    ("LNT-007", "Beeswax Candle Pack", 11.5),
]
CUSTOMERS = [f"c_{i:02d}" for i in range(1, 13)]
TASK = (
    "Pull the orders for week {week}, compute how RFM segments shifted versus the previous "
    "week and the top 5 products, then post a 5-sentence summary to #eng on Slack. "
    "Use the shop and slack tools only."
)

STR: dict[str, Any] = {"type": "string"}


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


TOOLS = [
    ToolSchema(
        server="shop",
        name="week_range",
        effect="read",
        input_schema=_schema({"week": STR}, ["week"]),
    ),
    ToolSchema(
        server="shop",
        name="list_orders",
        effect="read",
        input_schema=_schema({"since": STR, "until": STR}, ["since", "until"]),
    ),
    ToolSchema(
        server="shop",
        name="top_products",
        effect="read",
        input_schema=_schema(
            {"since": STR, "until": STR, "n": {"type": "integer", "default": 5}}, ["since", "until"]
        ),
    ),
    ToolSchema(
        server="shop",
        name="rfm_snapshot",
        effect="read",
        input_schema=_schema({"as_of": STR}, ["as_of"]),
    ),
    ToolSchema(server="shop", name="list_customers", effect="read", input_schema=_schema({}, [])),
    ToolSchema(
        server="slack",
        name="post_message",
        effect="write",
        input_schema=_schema({"channel": STR, "text": STR}, ["channel", "text"]),
    ),
    ToolSchema(
        server="builtin",
        name="Read",
        effect="read",
        input_schema=_schema({"file_path": STR}, ["file_path"]),
    ),
]


def week_data(week: str) -> dict[str, Any]:
    start, end = WEEKS[week]
    rng = random.Random(int(week[-2:]))
    d0 = datetime.fromisoformat(start)
    orders: list[dict[str, Any]] = []
    for i in range(8):
        items: list[dict[str, Any]] = []
        for sku, _, price in rng.sample(PRODUCTS, rng.randint(1, 3)):
            items.append({"sku": sku, "qty": rng.randint(1, 4), "price": price})
        total = round(sum(it["qty"] * it["price"] for it in items), 2)
        created = d0 + timedelta(days=rng.randint(0, 6), hours=rng.randint(8, 20))
        orders.append(
            {
                "id": f"o_{week[-2:]}{i:02d}",
                "customer_id": rng.choice(CUSTOMERS),
                "created_at": created.isoformat() + "Z",
                "total": total,
                "items": items,
            }
        )
    revenue: dict[str, float] = {}
    units: dict[str, int] = {}
    for o in orders:
        for it in o["items"]:
            revenue[it["sku"]] = round(revenue.get(it["sku"], 0.0) + it["qty"] * it["price"], 2)
            units[it["sku"]] = units.get(it["sku"], 0) + it["qty"]
    names = {sku: name for sku, name, _ in PRODUCTS}
    top = sorted(
        ({"sku": s, "name": names[s], "revenue": r, "units": units[s]} for s, r in revenue.items()),
        key=lambda p: (-p["revenue"], p["sku"]),
    )[:5]
    as_of = (d0 - timedelta(days=1)).date().isoformat()
    rfm = [
        {
            "customer_id": c,
            "r": rng.randint(1, 60),
            "f": rng.randint(1, 9),
            "m": round(rng.uniform(20, 400), 2),
        }
        for c in rng.sample(CUSTOMERS, 6)
    ]
    total_rev = round(sum(o["total"] for o in orders), 2)
    summary = (
        f"Week {week} closed with {len(orders)} orders totalling ${total_rev:.2f}. "
        f"{top[0]['name']} led revenue at ${top[0]['revenue']:.2f} across {top[0]['units']} units. "
        f"Compared with the snapshot as of {as_of}, {rng.randint(1, 3)} customers moved up "
        "a frequency band. "
        f"Returning customers placed {rng.randint(2, 5)} of the orders. "
        f"Full top-5 list and RFM table are in the shop dashboard."
    )
    return {
        "start": start,
        "end": end,
        "orders": orders,
        "top": top,
        "as_of": as_of,
        "rfm": rfm,
        "summary": summary,
    }


def build(trace_id: str, week: str, created_at: datetime, *, with_read: bool, cost: float) -> Trace:
    d = week_data(week)
    events: list[Thought | ToolCall] = []
    calls = 0

    def call(
        tool: str, args: dict[str, Any], result: Any, kind: ResultKind = "json", ms: int = 120
    ) -> None:
        nonlocal calls
        calls += 1
        events.append(
            ToolCall(
                idx=len(events),
                call_id=f"toolu_{calls:02d}",
                tool=tool,
                args=args,
                result=result,
                result_kind=kind,
                started_at=created_at + timedelta(seconds=3 * calls),
                duration_ms=ms,
            )
        )

    events.append(
        Thought(
            idx=0,
            text=f"I'll resolve the week range for {week} first, then pull the orders, "
            "the top products and the previous RFM snapshot.",
        )
    )
    call("shop.week_range", {"week": week}, {"start": d["start"], "end": d["end"]}, ms=40)
    call("shop.list_orders", {"since": d["start"], "until": d["end"]}, d["orders"], ms=210)
    call("shop.top_products", {"since": d["start"], "until": d["end"], "n": 5}, d["top"], ms=95)
    call("shop.rfm_snapshot", {"as_of": d["as_of"]}, d["rfm"], ms=130)
    if with_read:
        call("builtin.Read", {"file_path": "task.md"}, TASK.format(week=week), kind="text", ms=5)
    events.append(
        Thought(
            idx=len(events),
            text="Comparing the snapshot with this week's orders I can summarize "
            "the shift and the top products in five sentences.",
        )
    )
    call(
        "slack.post_message",
        {"channel": "#eng", "text": d["summary"]},
        {"ok": True, "ts": f"{int(created_at.timestamp())}.000100"},
        ms=180,
    )
    return Trace(
        id=trace_id,
        workflow="weekly-report",
        created_at=created_at,
        adapter="claude-stream",
        harness={"name": "claude-code", "version": "2.1.316", "model": "claude-sonnet-5-5"},
        task_prompt=TASK.format(week=week),
        inputs={"week": week},
        tools=TOOLS,
        events=list(events),
        outcome=Outcome(
            status="success",
            final_text="Posted the weekly summary to #eng.",
            usage=Usage(
                input_tokens=5100 + calls * 37,
                cache_read_tokens=12000,
                cache_creation_tokens=800,
                output_tokens=900 + calls * 11,
            ),
            cost_usd=cost,
            duration_ms=18000 + calls * 250,
            num_turns=calls + 1,
        ),
        session_id=f"s_{trace_id}",
    )


def broken_from(t1: Trace) -> Trace:
    data = t1.model_dump(mode="json")
    data["id"] = "t_broken"
    data["session_id"] = "s_t_broken"
    ev = data["events"]
    ev[2], ev[3] = ev[3], ev[2]  # swap list_orders and top_products
    ev.append(
        {
            "kind": "tool_call",
            "idx": 0,
            "call_id": "toolu_99",
            "tool": "shop.list_customers",
            "args": {},
            "result": [
                {
                    "id": c,
                    "name": f"Customer {c}",
                    "segment": "loyal",
                    "first_order_at": "2026-01-05",
                }
                for c in CUSTOMERS[:3]
            ],
            "result_kind": "json",
            "is_error": False,
            "started_at": None,
            "duration_ms": 60,
        }
    )
    for i, e in enumerate(ev):
        e["idx"] = i
    return Trace.model_validate(data)


def main() -> None:
    specs = [
        ("t1", "2026-W42", datetime(2026, 10, 19, 9, 0, tzinfo=UTC), False, 0.031),
        ("t2", "2026-W43", datetime(2026, 10, 26, 9, 0, tzinfo=UTC), True, 0.034),
        ("t3", "2026-W44", datetime(2026, 11, 2, 9, 0, tzinfo=UTC), False, 0.030),
    ]
    traces = [build(tid, week, at, with_read=rd, cost=cost) for tid, week, at, rd, cost in specs]
    traces.append(broken_from(traces[0]))
    OUT.mkdir(parents=True, exist_ok=True)
    for t in traces:
        write_json(OUT / f"{t.id}.json", t.model_dump(mode="json"))
        print("wrote", OUT / f"{t.id}.json", len(t.calls), "calls")


if __name__ == "__main__":
    main()
