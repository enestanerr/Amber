"""Seeded generator for every weekly-report fixture (synthetic "Northwind Lanterns Co." data).

    uv run python tests/fixtures/gen_weekly_report.py

Writes:

* ``claude_stream/weekly_report_run{1,2,3}.jsonl`` — three ``claude -p`` stream-json runs
  (run 2 has one exploratory ``Read``; each file has a ``stream_event``, a
  ``rate_limit_event`` and one ANSI-prefixed line that the recorder must ignore/strip);
* ``traces/weekly-report/{t1,t2,t3}.json`` — the mapper's golden output for those runs
  (fixed ids and timestamps) after schema enrichment with the mock servers' tool schemas;
* ``traces/weekly-report/t_broken.json`` — t1 with steps 2 and 3 swapped plus an extra call;
* ``claude_transcript/session.jsonl`` — one Claude Code transcript with a compaction
  boundary, a sidechain line, a foreign-session line and 4 tool calls;
* ``mcp_proxy/shop-1234.jsonl``, ``mcp_proxy/slack-1235.jsonl`` — proxy fragments.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from amber.adapters.base import RecordRequest
from amber.adapters.claude_stream import record_from_lines
from amber.adapters.enrich import apply_tool_schemas
from amber.trace.models import ToolSchema, Trace
from amber.util.json import dumps_compact, write_json

ROOT = Path(__file__).parent
TRACES = ROOT / "traces" / "weekly-report"
STREAM = ROOT / "claude_stream"
TRANSCRIPT = ROOT / "claude_transcript"
PROXY = ROOT / "mcp_proxy"

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
MODEL = "claude-sonnet-5-5"
STR: dict[str, Any] = {"type": "string"}


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


SHOP_SCHEMAS: dict[str, dict[str, Any]] = {
    "week_range": _schema({"week": STR}, ["week"]),
    "list_orders": _schema({"since": STR, "until": STR}, ["since", "until"]),
    "top_products": _schema(
        {"since": STR, "until": STR, "n": {"type": "integer", "default": 5}}, ["since", "until"]
    ),
    "rfm_snapshot": _schema({"as_of": STR}, ["as_of"]),
    "list_customers": _schema({}, []),
}
SLACK_SCHEMAS: dict[str, dict[str, Any]] = {
    "post_message": _schema({"channel": STR, "text": STR}, ["channel", "text"]),
    "list_channels": _schema({}, []),
}
TOOLS: list[ToolSchema] = [
    *[
        ToolSchema(server="shop", name=n, effect="read", input_schema=s)
        for n, s in SHOP_SCHEMAS.items()
    ],
    ToolSchema(
        server="slack",
        name="post_message",
        effect="write",
        input_schema=SLACK_SCHEMAS["post_message"],
    ),
    ToolSchema(
        server="slack",
        name="list_channels",
        effect="read",
        input_schema=SLACK_SCHEMAS["list_channels"],
    ),
    ToolSchema(
        server="builtin",
        name="Read",
        effect="read",
        input_schema=_schema({"file_path": STR}, ["file_path"]),
    ),
]
INIT_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    *[f"mcp__shop__{n}" for n in SHOP_SCHEMAS],
    *[f"mcp__slack__{n}" for n in SLACK_SCHEMAS],
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
        "Full top-5 list and RFM table are in the shop dashboard."
    )
    thought1 = (
        f"I'll resolve the week range for {week} first, then pull the orders, "
        "the top products and the previous RFM snapshot."
    )
    thought2 = (
        "Comparing the snapshot with this week's orders I can summarize "
        "the shift and the top products in five sentences."
    )
    return {
        "start": start,
        "end": end,
        "orders": orders,
        "top": top,
        "as_of": as_of,
        "rfm": rfm,
        "summary": summary,
        "thought1": thought1,
        "thought2": thought2,
        "calls": [
            ("mcp__shop__week_range", {"week": week}, {"start": start, "end": end}),
            ("mcp__shop__list_orders", {"since": start, "until": end}, orders),
            ("mcp__shop__top_products", {"since": start, "until": end, "n": 5}, top),
            ("mcp__shop__rfm_snapshot", {"as_of": as_of}, rfm),
        ],
        "post": (
            "mcp__slack__post_message",
            {"channel": "#eng", "text": summary},
            # the Slack timestamp differs per run (volatile result path in the skeleton)
            {"ok": True, "ts": f"{int(datetime.fromisoformat(end).timestamp()) + 32400}.000100"},
        ),
    }


# --- stream-json ---------------------------------------------------------------


def _assistant(
    session_id: str, msg_id: str, content: list[dict[str, Any]], usage: dict[str, int]
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "session_id": session_id,
        "message": {
            "id": msg_id,
            "role": "assistant",
            "model": MODEL,
            "content": content,
            "usage": usage,
        },
    }


def _tool_result(session_id: str, call_id: str, content: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "session_id": session_id,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": content}],
        },
    }


def stream_lines(week: str, *, session_id: str, with_read: bool, cost: float) -> list[str]:
    d = week_data(week)
    usage = {"input_tokens": 1200, "output_tokens": 40}
    out: list[dict[str, Any]] = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
            "model": MODEL,
            "tools": INIT_TOOLS,
            "mcp_servers": [
                {"name": "shop", "status": "connected"},
                {"name": "slack", "status": "connected"},
            ],
            "cwd": "/work/weekly-report",
        },
        _assistant(session_id, "msg_1", [{"type": "text", "text": d["thought1"]}], usage),
    ]
    n = 0
    calls = list(d["calls"])
    if with_read:
        calls.append(("Read", {"file_path": "task.md"}, TASK.format(week=week)))
    for name, args, result in calls:
        n += 1
        call_id = f"toolu_{n:02d}"
        block = {"type": "tool_use", "id": call_id, "name": name, "input": args}
        out.append(_assistant(session_id, f"msg_{n + 1}", [block], usage))
        content: Any = (
            result if isinstance(result, str) else [{"type": "text", "text": json.dumps(result)}]
        )
        out.append(_tool_result(session_id, call_id, content))
        if n == 2:
            out.append(
                {
                    "type": "stream_event",
                    "session_id": session_id,
                    "event": {"type": "message_start"},
                }
            )
            out.append(
                {
                    "type": "rate_limit_event",
                    "session_id": session_id,
                    "rate_limit_info": {"status": "allowed"},
                }
            )
    out.append(_assistant(session_id, "msg_t2", [{"type": "text", "text": d["thought2"]}], usage))
    n += 1
    name, args, result = d["post"]
    call_id = f"toolu_{n:02d}"
    block = {"type": "tool_use", "id": call_id, "name": name, "input": args}
    out.append(_assistant(session_id, f"msg_{n + 1}", [block], usage))
    out.append(_tool_result(session_id, call_id, [{"type": "text", "text": json.dumps(result)}]))
    out.append(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": session_id,
            "duration_ms": 18000 + n * 250,
            "num_turns": n + 1,
            "total_cost_usd": cost,
            "result": "Posted the weekly summary to #eng.",
            "usage": {
                "input_tokens": 5100 + n * 37,
                "cache_read_input_tokens": 12000,
                "cache_creation_input_tokens": 800,
                "output_tokens": 900 + n * 11,
            },
        }
    )
    lines = [dumps_compact(o) for o in out]
    # one ANSI-prefixed line (terminal clear sequence) the recorder must strip
    idx = next(i for i, o in enumerate(out) if o["type"] == "rate_limit_event")
    lines[idx] = "\x1b[2K" + lines[idx]
    return lines


# --- transcript ------------------------------------------------------------------


def transcript_lines() -> list[str]:
    week = "2026-W42"
    d = week_data(week)
    sid = "sess-A"
    t0 = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    out: list[dict[str, Any]] = []
    counter = 0

    def line(kind: str, body: dict[str, Any], *, parent: str | None, offset: int) -> str:
        nonlocal counter
        counter += 1
        uid = f"u{counter}"
        stamp = (t0 + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")
        out.append(
            {
                "type": kind,
                "uuid": uid,
                "parentUuid": parent,
                "sessionId": sid,
                "isSidechain": False,
                "timestamp": stamp,
                "cwd": "/work/weekly-report",
                "version": "2.1.316",
                "gitBranch": "main",
                **body,
            }
        )
        return uid

    def assistant(content: list[dict[str, Any]], out_tokens: int) -> dict[str, Any]:
        return {
            "message": {
                "role": "assistant",
                "model": MODEL,
                "content": content,
                "usage": {"input_tokens": 1600, "output_tokens": out_tokens},
            }
        }

    prev = line(
        "user",
        {"message": {"role": "user", "content": TASK.format(week=week)}},
        parent=None,
        offset=0,
    )
    prev = line(
        "assistant", assistant([{"type": "text", "text": d["thought1"]}], 30), parent=prev, offset=2
    )
    calls = [d["calls"][0], d["calls"][1], d["calls"][2], d["post"]]
    for n, (name, args, result) in enumerate(calls, start=1):
        if n == 3:
            body = {
                "subtype": "compact_boundary",
                "content": "Conversation compacted",
                "isMeta": False,
            }
            prev = line("system", body, parent=prev, offset=n * 10)
        block = {"type": "tool_use", "id": f"toolu_{n:02d}", "name": name, "input": args}
        prev = line("assistant", assistant([block], 50), parent=prev, offset=n * 10 + 1)
        result_block = {
            "type": "tool_result",
            "tool_use_id": f"toolu_{n:02d}",
            "content": [{"type": "text", "text": json.dumps(result)}],
        }
        body = {
            "message": {"role": "user", "content": [result_block]},
            "toolUseResult": {"summary": "ok"},
        }
        prev = line("user", body, parent=prev, offset=n * 10 + 2)
        if n == 2:
            out.append(
                {
                    "type": "assistant",
                    "uuid": "side1",
                    "parentUuid": prev,
                    "sessionId": sid,
                    "isSidechain": True,
                    "timestamp": "2026-10-19T09:00:25Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_side",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            }
                        ],
                    },
                }
            )
    final = [{"type": "text", "text": "Posted the weekly summary to #eng."}]
    line("assistant", assistant(final, 20), parent=prev, offset=60)
    out.insert(
        3,
        {
            "type": "user",
            "uuid": "f1",
            "parentUuid": None,
            "sessionId": "sess-OLD",
            "isSidechain": False,
            "timestamp": "2026-10-12T09:00:00Z",
            "message": {
                "role": "user",
                "content": "An older prompt copied from a previous session.",
            },
        },
    )
    return [dumps_compact(o) for o in out]


# --- proxy fragments -------------------------------------------------------------


def proxy_fragments() -> dict[str, list[str]]:
    d = week_data("2026-W42")
    t0 = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)

    def ts(seconds: int) -> str:
        return (t0 + timedelta(seconds=seconds)).isoformat()

    def tools(
        server: str, schemas: dict[str, dict[str, Any]], read_only: dict[str, bool]
    ) -> dict[str, Any]:
        listing = [
            {
                "name": n,
                "description": f"{n} (mock)",
                "input_schema": s,
                "annotations": {
                    "read_only_hint": read_only[n],
                    "destructive_hint": not read_only[n],
                },
            }
            for n, s in schemas.items()
        ]
        return {"kind": "tools", "ts": ts(0), "server": server, "tools": listing}

    def call(server: str, name: str, args: Any, result: Any, *, at: int, ms: int) -> dict[str, Any]:
        return {
            "kind": "call",
            "ts": ts(at),
            "server": server,
            "tool": name.split("__")[-1],
            "args": args,
            "result": result,
            "result_kind": "json",
            "is_error": False,
            "duration_ms": ms,
            "schema_hash": "",
        }

    shop = [tools("shop", SHOP_SCHEMAS, dict.fromkeys(SHOP_SCHEMAS, True))]
    for i, (name, args, result) in enumerate(d["calls"], start=1):
        shop.append(call("shop", name, args, result, at=i * 5, ms=40 + i))
    name, args, result = d["post"]
    slack = [
        tools("slack", SLACK_SCHEMAS, {"post_message": False, "list_channels": True}),
        call("slack", name, args, result, at=40, ms=90),
    ]
    return {
        "shop-1234.jsonl": [dumps_compact(o) for o in shop],
        "slack-1235.jsonl": [dumps_compact(o) for o in slack],
    }


# --- traces (mapper goldens) ----------------------------------------------------


def trace_from_stream(trace_id: str, week: str, lines: list[str], created_at: datetime) -> Trace:
    request = RecordRequest(workflow="weekly-report", agent_command=[], inputs={"week": week})
    rec = record_from_lines(
        request, lines, task_prompt=TASK.format(week=week), trace_id=trace_id, created_at=created_at
    )
    trace = apply_tool_schemas(rec.trace, TOOLS)
    trace.harness["version"] = "2.1.316"
    return trace


def broken_from(t1: Trace) -> Trace:
    data = t1.model_dump(mode="json")
    data["id"] = "t_broken"
    data["session_id"] = "s_t_broken"
    ev = data["events"]
    calls = [i for i, e in enumerate(ev) if e["kind"] == "tool_call"]
    a, b = calls[1], calls[2]
    ev[a], ev[b] = ev[b], ev[a]  # swap list_orders and top_products
    ev.append(
        {
            "kind": "tool_call",
            "idx": 0,
            "call_id": "toolu_99",
            "parent_call_id": None,
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


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    specs = [
        ("t1", 1, "2026-W42", datetime(2026, 10, 19, 9, 0, tzinfo=UTC), False, 0.031),
        ("t2", 2, "2026-W43", datetime(2026, 10, 26, 9, 0, tzinfo=UTC), True, 0.034),
        ("t3", 3, "2026-W44", datetime(2026, 11, 2, 9, 0, tzinfo=UTC), False, 0.030),
    ]
    traces: list[Trace] = []
    for tid, run, week, at, with_read, cost in specs:
        lines = stream_lines(week, session_id=f"s_{tid}", with_read=with_read, cost=cost)
        _write_lines(STREAM / f"weekly_report_run{run}.jsonl", lines)
        traces.append(trace_from_stream(tid, week, lines, at))
    traces.append(broken_from(traces[0]))
    for t in traces:
        write_json(TRACES / f"{t.id}.json", t.model_dump(mode="json"))
        print("wrote", TRACES / f"{t.id}.json", len(t.calls), "calls")
    _write_lines(TRANSCRIPT / "session.jsonl", transcript_lines())
    for name, lines in proxy_fragments().items():
        _write_lines(PROXY / name, lines)
    print("wrote stream, transcript and proxy fixtures")


if __name__ == "__main__":
    main()
