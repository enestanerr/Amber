"""Seeded synthetic data for the examples (``docs/06-examples.md``).

Everything here is fictional: the company is "Northwind Lanterns Co.", products are lanterns,
customer names come from short word lists. Same seed → same data (asserted by tests).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

PRODUCTS: list[tuple[str, str, float]] = [
    ("LNT-001", "Brass Hurricane Lantern", 48.0),
    ("LNT-002", "Copper Candle Lantern", 32.5),
    ("LNT-003", "Iron Garden Torch", 27.0),
    ("LNT-004", "Glass Storm Lamp", 54.0),
    ("LNT-005", "Tin Pocket Lantern", 14.0),
    ("LNT-006", "Oak Lantern Stand", 39.0),
    ("LNT-007", "Beeswax Candle Pack", 11.5),
    ("LNT-008", "Solar Path Lantern", 22.0),
    ("LNT-009", "Railway Signal Lamp", 89.0),
    ("LNT-010", "Paper Festival Lantern", 6.5),
    ("LNT-011", "Lighthouse Desk Lamp", 64.0),
    ("LNT-012", "Camping Lantern Mini", 18.0),
    ("LNT-013", "Wrought Wall Sconce", 42.0),
    ("LNT-014", "Replacement Glass Chimney", 9.0),
]
FIRST_NAMES = [
    "Dana", "Mira", "Teo", "Ilse", "Rafi", "Nia", "Jonas", "Sade", "Kai", "Lior", "Wren", "Ada",
    "Bram", "Elif", "Hugo", "Isa", "Jules", "Keziah", "Luca", "Maren", "Nuri", "Orla", "Pavel",
    "Quinn", "Rosa", "Sven", "Tamsin", "Umut", "Vera", "Yara",
]  # fmt: skip
LAST_NAMES = [
    "Oyelaran", "Castellane", "Brandvik", "Okonkwo", "Varga", "Lindqvist", "Marchetti", "Haddad",
    "Petrov", "Nakamura", "Fontaine", "Achterberg", "Sorensen", "Delgado", "Kowalski", "Mbeki",
    "Ruiz", "Tanaka", "Weiss", "Yilmaz",
]  # fmt: skip
SEGMENTS = ["new", "loyal", "lapsed", "wholesale"]
CATALOG_END = date(2026, 11, 1)  # the last Sunday covered by the shop data


@dataclass(frozen=True)
class Customer:
    id: str
    name: str
    segment: str
    first_order_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "segment": self.segment,
            "first_order_at": self.first_order_at,
        }


@dataclass(frozen=True)
class Order:
    id: str
    customer_id: str
    created_at: str  # ISO datetime, UTC "Z"
    total: float
    items: tuple[dict[str, Any], ...]

    def as_dict(self, *, total_key: str = "total") -> dict[str, Any]:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "created_at": self.created_at,
            total_key: self.total,
            "items": [dict(i) for i in self.items],
        }


def customers(seed: int = 7, n: int = 120) -> list[Customer]:
    rng = random.Random(seed)
    out: list[Customer] = []
    for i in range(1, n + 1):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        first = CATALOG_END - timedelta(days=rng.randint(60, 720))
        out.append(Customer(f"c_{i:03d}", name, rng.choice(SEGMENTS), first.isoformat()))
    return out


def orders(seed: int = 7, *, weeks: int = 12, per_week: int = 75) -> list[Order]:
    """~900 orders over the 12 weeks ending :data:`CATALOG_END` (Sunday)."""
    rng = random.Random(seed + 1)
    custs = customers(seed)
    start = CATALOG_END - timedelta(days=7 * weeks - 1)
    out: list[Order] = []
    n = 0
    for w in range(weeks):
        week_start = start + timedelta(days=7 * w)
        for _ in range(per_week):
            n += 1
            items: list[dict[str, Any]] = []
            for sku, _name, price in rng.sample(PRODUCTS, rng.randint(1, 3)):
                items.append({"sku": sku, "qty": rng.randint(1, 4), "price": price})
            total = round(sum(i["qty"] * i["price"] for i in items), 2)
            created = datetime.combine(week_start, datetime.min.time()) + timedelta(
                days=rng.randint(0, 6), hours=rng.randint(7, 21), minutes=rng.randint(0, 59)
            )
            out.append(
                Order(
                    id=f"o_{n:04d}",
                    customer_id=rng.choice(custs).id,
                    created_at=created.isoformat() + "Z",
                    total=total,
                    items=tuple(items),
                )
            )
    out.sort(key=lambda o: o.created_at)
    return out


def week_range(week: str) -> tuple[date, date]:
    """``"2026-W44"`` → (Monday, Sunday)."""
    year, _, num = week.partition("-W")
    monday = date.fromisocalendar(int(year), int(num), 1)
    return monday, monday + timedelta(days=6)


def digest(seed: int = 7) -> str:
    """Stable fingerprint of the generated data (tests assert determinism with it)."""
    import hashlib
    import json

    payload = [o.as_dict() for o in orders(seed)] + [c.as_dict() for c in customers(seed)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
