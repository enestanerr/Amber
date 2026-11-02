"""Mock shop MCP server for the weekly-report example (``docs/06-examples.md`` §1).

Tools: ``week_range``, ``list_orders``, ``list_customers``, ``top_products``, ``rfm_snapshot``.
Data: 12 weeks of seeded synthetic orders (``examples/_shared/synth.py``) loaded into an
in-memory SQLite database at startup. Scenario ``B`` (``make drift``) renames ``total`` to
``amount`` in ``list_orders`` results, which trips the result-shape guard of a compiled program.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

import synth
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp_common import scenario

mcp = MCPServer("shop")
READ = ToolAnnotations(read_only_hint=True)
SCENARIO = scenario()
_DB: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _DB
    if _DB is None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE customers (id TEXT PRIMARY KEY, name TEXT, segment TEXT,"
            " first_order_at TEXT);"
            "CREATE TABLE orders (id TEXT PRIMARY KEY, customer_id TEXT, created_at TEXT,"
            " total REAL);"
            "CREATE TABLE items (order_id TEXT, sku TEXT, qty INTEGER, price REAL);"
        )
        conn.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?)",
            [(c.id, c.name, c.segment, c.first_order_at) for c in synth.customers()],
        )
        for o in synth.orders():
            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?)",
                (o.id, o.customer_id, o.created_at, o.total),
            )
            conn.executemany(
                "INSERT INTO items VALUES (?, ?, ?, ?)",
                [(o.id, i["sku"], i["qty"], i["price"]) for i in o.items],
            )
        conn.commit()
        _DB = conn
    return _DB


def _span(since: str, until: str) -> tuple[str, str]:
    return f"{since}T00:00:00", f"{until}T23:59:59"


@mcp.tool(annotations=READ)
def week_range(week: str) -> dict[str, str]:
    """ISO week (e.g. 2026-W44) → {"start": Monday, "end": Sunday} as ISO dates."""
    start, end = synth.week_range(week)
    return {"start": start.isoformat(), "end": end.isoformat()}


@mcp.tool(annotations=READ)
def list_orders(since: str, until: str) -> list[dict[str, Any]]:
    """Orders created between two ISO dates (inclusive), with their line items."""
    lo, hi = _span(since, until)
    conn = db()
    rows = conn.execute(
        "SELECT * FROM orders WHERE created_at >= ? AND created_at <= ? ORDER BY created_at",
        (lo, hi),
    ).fetchall()
    total_key = "amount" if SCENARIO == "B" else "total"
    out: list[dict[str, Any]] = []
    for r in rows:
        items = conn.execute(
            "SELECT sku, qty, price FROM items WHERE order_id = ?", (r["id"],)
        ).fetchall()
        out.append(
            {
                "id": r["id"],
                "customer_id": r["customer_id"],
                "created_at": r["created_at"],
                total_key: r["total"],
                "items": [{"sku": i["sku"], "qty": i["qty"], "price": i["price"]} for i in items],
            }
        )
    return out


@mcp.tool(annotations=READ)
def list_customers() -> list[dict[str, Any]]:
    """All customers with their segment and first order date."""
    return [dict(r) for r in db().execute("SELECT * FROM customers ORDER BY id").fetchall()]


@mcp.tool(annotations=READ)
def top_products(since: str, until: str, n: int = 5) -> list[dict[str, Any]]:
    """Top products by revenue between two ISO dates."""
    lo, hi = _span(since, until)
    names = {sku: name for sku, name, _ in synth.PRODUCTS}
    rows = (
        db()
        .execute(
            "SELECT i.sku AS sku, SUM(i.qty * i.price) AS revenue, SUM(i.qty) AS units "
            "FROM items i JOIN orders o ON o.id = i.order_id "
            "WHERE o.created_at >= ? AND o.created_at <= ? GROUP BY i.sku"
            " ORDER BY revenue DESC, sku LIMIT ?",
            (lo, hi, n),
        )
        .fetchall()
    )
    return [
        {
            "sku": r["sku"],
            "name": names.get(r["sku"], r["sku"]),
            "revenue": round(r["revenue"], 2),
            "units": r["units"],
        }
        for r in rows
    ]


@mcp.tool(annotations=READ)
def rfm_snapshot(as_of: str) -> list[dict[str, Any]]:
    """Recency (days since last order), frequency and monetary value per customer as of a date."""
    cutoff = date.fromisoformat(as_of)
    window_start = (cutoff - timedelta(days=90)).isoformat()
    rows = (
        db()
        .execute(
            "SELECT customer_id, MAX(created_at) AS last_at, COUNT(*) AS f, SUM(total) AS m "
            "FROM orders WHERE created_at >= ? AND created_at <= ?"
            " GROUP BY customer_id ORDER BY customer_id",
            (window_start, f"{as_of}T23:59:59"),
        )
        .fetchall()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        last = date.fromisoformat(str(r["last_at"])[:10])
        out.append(
            {
                "customer_id": r["customer_id"],
                "r": (cutoff - last).days,
                "f": r["f"],
                "m": round(r["m"], 2),
            }
        )
    return out


if __name__ == "__main__":
    mcp.run(transport="stdio")
