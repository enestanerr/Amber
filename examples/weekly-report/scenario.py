"""Switch the weekly-report world between scenario A (baseline) and B (drift).

uv run python scenario.py B      # make drift: list_orders returns `amount` instead of `total`
uv run python scenario.py A      # back to the baseline
uv run python scenario.py        # print the current scenario and the data digest
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

import synth
from mcp_common import scenario, set_scenario


def main(argv: list[str]) -> None:
    here = Path(__file__).resolve().parent
    if argv:
        marker = set_scenario(argv[0], here)
        print(f"scenario {argv[0].upper()} written to {marker.name}")
    print(
        f"scenario: {scenario()} · orders: {len(synth.orders())} · "
        f"customers: {len(synth.customers())} · digest: {synth.digest()}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
