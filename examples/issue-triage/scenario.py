"""Seeded issue data for issue-triage, and the scenario switch.

    uv run python scenario.py        # print scenario + a summary
    uv run python scenario.py B      # make drift: label taxonomy area:* -> kind/*
    uv run python scenario.py A

Recordings use ``--input since=2026-10-27`` / ``2026-10-28`` / ``2026-10-29``: the issues were
"created" over ten days, so each day a different slice is new.
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from mcp_common import scenario, set_scenario

AREAS = ["api", "ui", "docs", "build", "perf", "auth"]
TITLES: dict[str, list[str]] = {
    "api": [
        "Lantern search endpoint returns 500 for empty query",
        "Pagination cursor resets after 50 orders",
        "Webhook retries ignore backoff header",
        "Order totals rounded differently than invoice",
        "Rate limit headers missing on /catalog",
        "Bulk export truncates at 1000 rows",
        "API key rotation drops in-flight requests",
    ],
    "ui": [
        "Checkout button overlaps footer on narrow screens",
        "Dark mode loses product images",
        "Cart badge count stale after remove",
        "Filter chips not keyboard accessible",
        "Price shows NaN when stock is zero",
        "Lantern gallery thumbnails blurry on retina",
        "Address form loses state on back navigation",
    ],
    "docs": [
        "Quickstart references removed `--legacy` flag",
        "Webhook payload example is outdated",
        "Broken link in shipping policy page",
        "Typo in RFM glossary entry",
        "Missing docs for bulk label import",
        "Changelog not updated for 2.4",
        "Install guide skips Node requirement",
    ],
    "build": [
        "CI flaky on Windows runner",
        "Docker image misses tzdata",
        "Release workflow fails on tag with suffix",
        "Lockfile drifts after uv sync",
        "Test suite leaks temp files",
        "Coverage upload step times out",
        "Pre-commit hook ignores docs",
    ],
    "perf": [
        "Catalog page loads 3s on cold cache",
        "RFM snapshot query scans full orders table",
        "Image resize blocks the event loop",
        "Memory grows during bulk export",
        "Slow top_products with 14 SKUs",
        "Search debounce too short",
    ],
    "auth": [
        "Session expires during checkout",
        "OAuth callback rejects state with plus sign",
        "Password reset link valid for 24h instead of 1h",
        "2FA code accepted twice",
        "Admin role not revoked on deactivation",
        "Login form leaks user existence",
    ],
}
DUPLICATES = [(3, 17), (8, 25), (12, 31), (20, 36)]  # (original, duplicate)
AUTHORS = ["dana-o", "mira-c", "teo-b", "ilse-o", "rafi-v", "nia-l", "jonas-m", "sade-h"]
BASE_DAY = date(2026, 10, 30)


def issues(seed: int = 11) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    pool: list[tuple[str, str]] = [(area, t) for area in AREAS for t in TITLES[area]]
    rng.shuffle(pool)
    out: list[dict[str, Any]] = []
    for n in range(1, 41):
        area, title = pool[(n - 1) % len(pool)]
        created = BASE_DAY - timedelta(days=rng.randint(0, 9))
        body = (
            f"Reported by {rng.choice(AUTHORS)}: {title.lower()}. Steps to reproduce are in the "
            f"attached log. Affects the {area} area of Northwind Lanterns Co."
        )
        out.append(
            {
                "number": n,
                "title": title,
                "body": body,
                "labels": [],
                "state": "open",
                "created_at": created.isoformat(),
                "area": area,
            }
        )
    for original, dup in DUPLICATES:
        src = next(i for i in out if i["number"] == original)
        tgt = next(i for i in out if i["number"] == dup)
        tgt["title"] = src["title"] + " (again)"
        tgt["body"] = "Same as before: " + src["body"]
        tgt["area"] = src["area"]
        # a duplicate is always reported after its original ("duplicates an older one")
        if tgt["created_at"] < src["created_at"]:
            tgt["created_at"], src["created_at"] = src["created_at"], tgt["created_at"]
        elif tgt["created_at"] == src["created_at"]:
            earlier = date.fromisoformat(src["created_at"]) - timedelta(days=1)
            src["created_at"] = earlier.isoformat()
    return out


def main(argv: list[str]) -> None:
    here = Path(__file__).resolve().parent
    if argv:
        marker = set_scenario(argv[0], here)
        print(f"scenario {argv[0].upper()} written to {marker}")
    data = issues()
    days = sorted({i["created_at"] for i in data})
    print(
        f"scenario: {scenario()} · issues: {len(data)} · areas: {len(AREAS)} · "
        f"days: {days[0]}..{days[-1]}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
