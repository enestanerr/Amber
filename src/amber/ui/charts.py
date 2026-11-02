"""Server-rendered SVG charts (``docs/07-panel.md`` §Charts).

Bars ≤ 24 px, 4 px radius on the data end only, hairline grid at 12 % opacity, 12 px labels,
``<title>``/``<desc>`` for accessibility, colours by CSS class so light/dark come from the
stylesheet. Every chart has a table twin rendered by the template next to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

WIDTH = 720
HEIGHT = 220
PAD_LEFT = 56
PAD_RIGHT = 16
PAD_TOP = 20
PAD_BOTTOM = 36
BAR_MAX = 24
RADIUS = 4


@dataclass(frozen=True)
class TokenRow:
    label: str
    agent: int
    compiled: int


@dataclass(frozen=True)
class GuardRow:
    label: str
    soft: int
    hard: int


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    return float(((int(value) // magnitude) + 1) * magnitude)


def _bar_path(x: float, y: float, w: float, h: float) -> str:
    """Rounded top corners only (the data end)."""
    if h <= 0:
        return ""
    r = min(RADIUS, w / 2, h)
    return (
        f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z"
    )


def _frame(title: str, desc: str, body: str, *, legend: list[tuple[str, str]]) -> str:
    legend_svg = "".join(
        f'<g transform="translate({PAD_LEFT + i * 150},{HEIGHT - 10})">'
        f'<rect class="{cls}" width="12" height="12" rx="2"/>'
        f'<text x="18" y="10" class="label">{escape(name)}</text></g>'
        for i, (cls, name) in enumerate(legend)
    )
    return (
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-labelledby="t d" xmlns="http://www.w3.org/2000/svg">'
        f'<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>'
        f"{body}{legend_svg}</svg>"
    )


def _grid(max_value: float, plot_h: float) -> str:
    lines: list[str] = []
    for i in range(5):
        y = PAD_TOP + plot_h - plot_h * i / 4
        value = max_value * i / 4
        lines.append(
            f'<line class="grid" x1="{PAD_LEFT}" y1="{y:.1f}" '
            f'x2="{WIDTH - PAD_RIGHT}" y2="{y:.1f}"/>'
            f'<text class="label" x="{PAD_LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">'
            f"{int(value):,}</text>"
        )
    return "".join(lines)


def tokens_per_run_svg(rows: list[TokenRow], *, baseline: int | None = None) -> str:
    """Grouped bars: agent baseline (series 1) vs compiled llm tokens (series 2) per run,
    with the median agent baseline as a dashed reference line."""
    if not rows:
        return _frame("Tokens per run", "No runs yet.", "", legend=[])
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM - 16
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    max_value = _nice_max(max(max(r.agent, r.compiled) for r in rows))
    group_w = plot_w / len(rows)
    bar_w = min(BAR_MAX, group_w / 3)
    parts = [_grid(max_value, plot_h)]
    for i, r in enumerate(rows):
        x0 = PAD_LEFT + i * group_w + group_w / 2 - bar_w
        for series, value, cls in ((0, r.agent, "s1"), (1, r.compiled, "s2")):
            h = plot_h * value / max_value
            y = PAD_TOP + plot_h - h
            parts.append(
                f'<path class="{cls}" d="{_bar_path(x0 + series * bar_w, y, bar_w - 1, h)}">'
                f"<title>{escape(r.label)}: {value:,} tokens</title></path>"
            )
        parts.append(
            f'<text class="label" x="{x0 + bar_w:.1f}" y="{PAD_TOP + plot_h + 14}" '
            f'text-anchor="middle">{escape(r.label[:10])}</text>'
        )
    if baseline is not None and baseline > 0:
        y = PAD_TOP + plot_h - plot_h * min(baseline, max_value) / max_value
        parts.append(
            f'<line class="ref" x1="{PAD_LEFT}" y1="{y:.1f}" x2="{WIDTH - PAD_RIGHT}" y2="{y:.1f}" '
            f'stroke-dasharray="4 4"><title>agent baseline median {baseline:,}</title></line>'
        )
    desc = (
        f"{len(rows)} runs; agent baseline median {baseline or 0:,} tokens; "
        f"compiled llm tokens from {min(r.compiled for r in rows):,} "
        f"to {max(r.compiled for r in rows):,}."
    )
    return _frame(
        "Tokens per run",
        desc,
        "".join(parts),
        legend=[("s1", "agent baseline"), ("s2", "compiled")],
    )


def guard_events_svg(rows: list[GuardRow]) -> str:
    """Stacked bars per bucket: soft (series 4) under hard (series 2)."""
    if not rows:
        return _frame("Guard events", "No guard events yet.", "", legend=[])
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM - 16
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    max_value = _nice_max(max(r.soft + r.hard for r in rows))
    group_w = plot_w / len(rows)
    bar_w = min(BAR_MAX, group_w / 2)
    parts = [_grid(max_value, plot_h)]
    for i, r in enumerate(rows):
        x = PAD_LEFT + i * group_w + group_w / 2 - bar_w / 2
        soft_h = plot_h * r.soft / max_value
        hard_h = plot_h * r.hard / max_value
        base = PAD_TOP + plot_h
        if r.soft:
            parts.append(
                f'<rect class="s4" x="{x:.1f}" y="{base - soft_h:.1f}" '
                f'width="{bar_w:.1f}" height="{soft_h:.1f}">'
                f"<title>{escape(r.label)}: {r.soft} soft</title></rect>"
            )
        if r.hard:
            parts.append(
                f'<path class="s2" d="{_bar_path(x, base - soft_h - hard_h, bar_w, hard_h)}">'
                f"<title>{escape(r.label)}: {r.hard} hard</title></path>"
            )
        parts.append(
            f'<text class="label" x="{x + bar_w / 2:.1f}" y="{base + 14}" text-anchor="middle">'
            f"{escape(r.label[:10])}</text>"
        )
    total_soft = sum(r.soft for r in rows)
    total_hard = sum(r.hard for r in rows)
    desc = f"{len(rows)} buckets; {total_soft} soft and {total_hard} hard guard events."
    return _frame(
        "Guard events over time", desc, "".join(parts), legend=[("s4", "soft"), ("s2", "hard")]
    )
