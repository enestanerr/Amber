"""Generate the static catalog site for price-watch and switch scenarios.

    uv run python scenario.py        # write site/ for the current scenario (A by default)
    uv run python scenario.py A2     # a price-update day: two prices change, markup unchanged
    uv run python scenario.py B      # make drift: prices change AND `.price` becomes `.cost`
    uv run python scenario.py A

``A`` (nothing changed) and ``A2`` (prices moved) are the days the agent is recorded on, so the
recordings contain both outcomes: no alert and an alert.

The site is six fictional lanterns with ``<span class="price">`` / ``<span class="stock">``
spans plus ``previous.json`` (the prices the agent compares against).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from mcp_common import scenario, set_scenario

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
PRODUCTS: list[dict[str, Any]] = [
    {"sku": "lantern-brass", "name": "Brass Hurricane Lantern", "price": 48.0, "stock": 12},
    {"sku": "lantern-copper", "name": "Copper Candle Lantern", "price": 32.5, "stock": 4},
    {"sku": "torch-iron", "name": "Iron Garden Torch", "price": 27.0, "stock": 20},
    {"sku": "lamp-storm", "name": "Glass Storm Lamp", "price": 54.0, "stock": 0},
    {"sku": "lantern-tin", "name": "Tin Pocket Lantern", "price": 14.0, "stock": 35},
    {"sku": "stand-oak", "name": "Oak Lantern Stand", "price": 39.0, "stock": 7},
]
UPDATE_PRICES = {"lantern-brass": 46.0, "torch-iron": 29.5}  # scenario A2
DRIFT_PRICES = {"lantern-brass": 52.0, "lamp-storm": 49.0, "stand-oak": 41.5}  # scenario B


def catalog(scenario_name: str) -> list[dict[str, Any]]:
    items = [dict(p) for p in PRODUCTS]
    if scenario_name == "A2":
        for item in items:
            item["price"] = UPDATE_PRICES.get(item["sku"], item["price"])
    if scenario_name == "B":
        for item in items:
            item["price"] = DRIFT_PRICES.get(item["sku"], item["price"])
            if item["sku"] == "lantern-copper":
                item["stock"] = 0
    return items


def _stock(n: int) -> str:
    return f"in stock: {n}" if n else "out of stock"


def render(scenario_name: str) -> str:
    price_class = "cost" if scenario_name == "B" else "price"
    rows = "\n".join(
        f'      <li class="product" data-sku="{p["sku"]}">'
        f'<span class="name">{p["name"]}</span> '
        f'<span class="{price_class}">${p["price"]:.2f}</span> '
        f'<span class="stock">{_stock(p["stock"])}</span></li>'
        for p in catalog(scenario_name)
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8">'
        "<title>Northwind Lanterns Co. — catalog</title></head>\n<body>\n"
        "  <h1>Northwind Lanterns Co. — catalog</h1>\n"
        f"  <p>Scenario {scenario_name}. All products and prices are fictional.</p>\n"
        f'  <ul id="catalog">\n{rows}\n  </ul>\n</body>\n</html>\n'
    )


def write_site(scenario_name: str) -> Path:
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(render(scenario_name), encoding="utf-8", newline="\n")
    previous = {p["sku"]: {"price": p["price"], "stock": p["stock"]} for p in PRODUCTS}
    (SITE / "previous.json").write_text(
        json.dumps(previous, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return SITE / "index.html"


def main(argv: list[str]) -> None:
    if argv:
        marker = set_scenario(argv[0], HERE)
        print(f"scenario {argv[0].upper()} written to {marker.name}")
    name = scenario()
    path = write_site(name)
    changed = [
        p["sku"]
        for p in catalog(name)
        if p["price"] != next(q["price"] for q in PRODUCTS if q["sku"] == p["sku"])
    ]
    where = path.relative_to(HERE).as_posix()
    print(f"scenario: {name} · wrote {where} · price changes vs previous: {changed or 'none'}")


if __name__ == "__main__":
    main(sys.argv[1:])
