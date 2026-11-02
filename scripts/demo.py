"""Record the README demo from a real session and render it as an animated SVG.

    uv run python scripts/demo.py record   # runs the real commands, writes docs/assets/demo.cast
    uv run python scripts/demo.py render   # docs/assets/demo.cast -> docs/assets/demo.svg

``record`` copies ``examples/weekly-report`` (and ``examples/_shared``) into ``.demo/`` together
with the live recordings already in ``examples/weekly-report/.amber/traces`` (make them first with
``make record``), then runs the story below for real: compile, a live run on a new week, drift,
the doctor, the agent fallback and the recompile. It spends model tokens (one Sonnet agent run,
two compiles, one Haiku judgment). Output is captured with real timestamps into an asciicast v2
file (``asciinema play docs/assets/demo.cast`` replays it). Only the typing of each command is
simulated.

``render`` turns the cast into a self-contained animated SVG (CSS keyframes, no scripts): idle
gaps are capped at ``--idle`` seconds, ANSI colours are kept, the window scrolls like a terminal.
``--freeze-at SECONDS`` renders a still frame instead, for checking the layout.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / ".demo"
CAST = REPO / "docs" / "assets" / "demo.cast"
SVG = REPO / "docs" / "assets" / "demo.svg"
COLS, ROWS = 100, 30

# (shown command, argv to run or None for a comment); argv runs inside .demo/weekly-report
STORY: list[tuple[str, list[str] | None]] = [
    ("# the agent did this workflow 3 times (make record): ~170k tokens per run", None),
    ("amber traces ls weekly-report", ["amber", "traces", "ls", "weekly-report"]),
    ("amber align weekly-report --show", ["amber", "align", "weekly-report", "--show"]),
    ("amber compile weekly-report", ["amber", "compile", "weekly-report"]),
    (
        "amber run weekly-report --input week=2026-W40 --allow-writes",
        ["amber", "run", "weekly-report", "--input", "week=2026-W40", "--allow-writes"],
    ),
    ("# the shop API now returns `amount` instead of `total`", None),
    ("make drift", ["make", "drift"]),
    (
        "amber run weekly-report --input week=2026-W41 --allow-writes",
        ["amber", "run", "weekly-report", "--input", "week=2026-W41", "--allow-writes"],
    ),
    ("amber doctor weekly-report", ["amber", "doctor", "weekly-report"]),
    (
        "amber run weekly-report --input week=2026-W41 --allow-writes --fallback agent",
        [
            "amber",
            "run",
            "weekly-report",
            "--input",
            "week=2026-W41",
            "--allow-writes",
            "--fallback",
            "agent",
        ],
    ),
    ("amber compile weekly-report --refresh", ["amber", "compile", "weekly-report", "--refresh"]),
    ("amber verify weekly-report", ["amber", "verify", "weekly-report"]),
    ("amber doctor weekly-report", ["amber", "doctor", "weekly-report"]),
]


# --- record -------------------------------------------------------------------------------


def _prepare() -> Path:
    src = REPO / "examples" / "weekly-report"
    traces = sorted((src / ".amber" / "traces" / "weekly-report").glob("*.json"))
    recorded = [
        t for t in traces if json.loads(t.read_text(encoding="utf-8")).get("origin") == "record"
    ]
    if len(recorded) < 2:
        raise SystemExit("record the example first: cd examples/weekly-report && make record")
    if DEMO.exists():
        shutil.rmtree(DEMO)
    work = DEMO / "weekly-report"
    ignore = shutil.ignore_patterns(".amber", ".outbox", "programs", ".scenario", "__pycache__")
    shutil.copytree(src, work, ignore=ignore)
    shutil.copytree(REPO / "examples" / "_shared", DEMO / "_shared", ignore=ignore)
    dest = work / ".amber" / "traces" / "weekly-report"
    dest.mkdir(parents=True)
    for t in recorded:
        shutil.copy(t, dest / t.name)
    return work


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "FORCE_COLOR": "1",
            "COLUMNS": str(COLS),
            "TERM": "xterm-256color",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "UV_NO_SYNC": "1",
        }
    )
    return env


def record() -> None:
    work = _prepare()
    events: list[tuple[float, str]] = []
    t0 = time.monotonic()
    clock = 0.0

    def emit(text: str, at: float | None = None) -> None:
        nonlocal clock
        clock = max(clock, at if at is not None else time.monotonic() - t0)
        events.append((round(clock, 3), text))

    amber = [sys.executable, "-m", "amber"]
    for shown, argv in STORY:
        emit("\x1b[1;32m$\x1b[0m ")
        for ch in shown:  # simulated typing (the only synthetic part of the cast)
            clock += 0.035
            events.append((round(clock, 3), ch))
        emit("\r\n", clock + 0.25)
        if argv is None:
            clock += 0.8
            continue
        cmd = amber + argv[1:] if argv[0] == "amber" else argv
        start = time.monotonic()
        proc = subprocess.Popen(
            cmd, cwd=work, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        assert proc.stdout is not None
        offset = clock - (start - t0)  # keep real gaps inside a command, after the typing
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            emit(line + "\r\n", time.monotonic() - t0 + offset)
        proc.wait()
        print(f"{shown}  -> exit {proc.returncode}", file=sys.stderr)
        clock += 1.2
    subprocess.run(["make", "undrift"], cwd=work, env=_env(), capture_output=True, check=False)
    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "idle_time_limit": 1.5,
        "title": "Amber: compile, run, drift, heal (weekly-report example)",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    CAST.parent.mkdir(parents=True, exist_ok=True)
    with CAST.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(header) + "\n")
        for t, text in events:
            f.write(json.dumps([t, "o", text], ensure_ascii=False) + "\n")
    print(f"wrote {CAST.relative_to(REPO)} ({len(events)} events, {clock:.0f} s)")


# --- render -------------------------------------------------------------------------------

PALETTE = {
    30: "#45475A", 31: "#F38BA8", 32: "#A6E3A1", 33: "#F9E2AF", 34: "#89B4FA",
    35: "#F5C2E7", 36: "#94E2D5", 37: "#BAC2DE", 90: "#7F849C", 91: "#F38BA8",
    92: "#A6E3A1", 93: "#F9E2AF", 94: "#89B4FA", 95: "#F5C2E7", 96: "#94E2D5", 97: "#CDD6F4",
}  # fmt: skip
FG, BG = "#CDD6F4", "#1E1E2E"
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _xterm256(n: int) -> str:
    if n < 16:
        return PALETTE.get(30 + n if n < 8 else 82 + n, FG)
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return f"#{steps[n // 36]:02X}{steps[(n // 6) % 6]:02X}{steps[n % 6]:02X}"
    gray = 8 + (n - 232) * 10
    return f"#{gray:02X}{gray:02X}{gray:02X}"


@dataclass
class Style:
    fg: str | None = None
    bold: bool = False
    dim: bool = False
    italic: bool = False

    def apply(self, params: str) -> None:
        codes = [int(p) if p else 0 for p in params.split(";")] if params else [0]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                self.fg, self.bold, self.dim, self.italic = None, False, False, False
            elif c == 1:
                self.bold = True
            elif c == 2:
                self.dim = True
            elif c == 3:
                self.italic = True
            elif c == 22:
                self.bold = self.dim = False
            elif c == 23:
                self.italic = False
            elif c == 39:
                self.fg = None
            elif c in PALETTE:
                self.fg = PALETTE[c]
            elif c == 38 and i + 2 < len(codes) and codes[i + 1] == 5:
                self.fg = _xterm256(codes[i + 2])
                i += 2
            elif c == 38 and i + 4 < len(codes) and codes[i + 1] == 2:
                r, g, b = codes[i + 2 : i + 5]
                self.fg = f"#{r:02X}{g:02X}{b:02X}"
                i += 4
            elif c == 48 and i + 1 < len(codes):  # background: skip its arguments
                i += 2 if codes[i + 1] == 5 else 4 if codes[i + 1] == 2 else 0
            i += 1


@dataclass
class Line:
    at: float
    spans: list[tuple[str, Style]] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    gone: float | None = None  # typing frames disappear when the next one shows

    def text(self) -> str:
        return "".join(s for s, _ in self.spans)


def _spans(text: str, style: Style) -> list[tuple[str, Style]]:
    out: list[tuple[str, Style]] = []
    pos = 0
    text = OTHER_CSI.sub(lambda m: m.group(0) if m.group(0).endswith("m") else "", text)
    for m in SGR.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], Style(**vars(style))))
        style.apply(m.group(1))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], Style(**vars(style))))
    return out


def _load(idle: float) -> tuple[list[Line], float]:
    rows = CAST.read_text(encoding="utf-8").splitlines()
    events = [json.loads(r) for r in rows[1:]]
    lines: list[Line] = []
    style = Style()
    current = ""
    last_raw = 0.0
    clock = 0.0
    typing: list[Line] = []
    for raw_t, _, text in events:
        clock += min(raw_t - last_raw, idle)
        last_raw = raw_t
        current += text
        if text.endswith("\r\n") or text.endswith("\n"):
            body = current.rstrip("\r\n")
            if typing:  # the finished command line replaces its last typing frame
                typing[-1].gone = clock
            lines.append(Line(clock, _spans(body, style)))
            current = ""
            typing = []
        elif len(text) == 1 and current.startswith("\x1b[1;32m$"):
            # one typing frame per few characters keeps the file small
            if len(current) % 3 == 0 or text == " ":
                frame = Line(clock, _spans(current, Style()))
                if typing:
                    typing[-1].gone = clock
                typing.append(frame)
                lines.append(frame)
    return lines, clock


def render(idle: float, freeze_at: float | None) -> None:
    lines, total = _load(idle)
    hold = 6.0
    duration = total + hold
    char_w, line_h, pad, top = 8.4, 18.5, 18, 44
    width = int(COLS * char_w + 2 * pad)
    height = int(ROWS * line_h + top + pad)

    # row of each line: typing frames share the row of the command they become
    row_of: list[int] = []
    row = -1
    for i in range(len(lines)):
        previous_typing = i > 0 and lines[i - 1].gone is not None
        if not previous_typing:
            row += 1
        row_of.append(row)

    def pct(t: float) -> str:
        return f"{min(max(t / duration * 100, 0.0), 100.0):.3f}%"

    css: list[str] = []
    body: list[str] = []
    scroll_points: list[tuple[float, int]] = []
    shown_rows = 0
    for i, ln in enumerate(lines):
        r = row_of[i]
        if r + 1 > shown_rows:
            shown_rows = r + 1
            if shown_rows > ROWS:
                scroll_points.append((ln.at, shown_rows - ROWS))
        name = f"l{i}"
        start, end = pct(ln.at), pct(ln.at + 0.01)
        if ln.gone is not None:
            g1, g2 = pct(ln.gone), pct(ln.gone + 0.01)
            frames = f"0%,{start}{{opacity:0}}{end},{g1}{{opacity:1}}{g2},100%{{opacity:0}}"
        else:
            frames = f"0%,{start}{{opacity:0}}{end},100%{{opacity:1}}"
        css.append(f"@keyframes {name}{{{frames}}}.{name}{{animation-name:{name}}}")
        tspans = []
        for text, st in ln.spans:
            if not text:
                continue
            attrs = [f'fill="{st.fg or FG}"']
            if st.bold:
                attrs.append('font-weight="700"')
            if st.dim:
                attrs.append('opacity="0.6"')
            if st.italic:
                attrs.append('font-style="italic"')
            tspans.append(f"<tspan {' '.join(attrs)}>{html.escape(text, quote=False)}</tspan>")
        y = pad + top + (r + 1) * line_h - 5
        body.append(f'<text class="ln {name}" x="{pad}" y="{y:.1f}">{"".join(tspans)}</text>')

    scroll = ["0%{transform:translateY(0)}"]
    prev = 0
    for t, n in scroll_points:
        scroll.append(f"{pct(t)}{{transform:translateY({-prev * line_h:.1f}px)}}")
        scroll.append(f"{pct(t + 0.01)}{{transform:translateY({-n * line_h:.1f}px)}}")
        prev = n
    scroll.append(f"100%{{transform:translateY({-prev * line_h:.1f}px)}}")

    freeze = ""
    if freeze_at is not None:
        freeze = f".ln,.scroll{{animation-delay:-{freeze_at:.2f}s!important;animation-play-state:paused!important}}"
    style = (
        f".ln{{opacity:0;animation-duration:{duration:.2f}s;animation-iteration-count:infinite;"
        f"animation-timing-function:linear;white-space:pre;"
        f"font:13.5px ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace}}"
        f".scroll{{animation:scroll {duration:.2f}s linear infinite}}"
        f"@keyframes scroll{{{''.join(scroll)}}}" + "".join(css) + freeze
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Amber demo: compile three recordings, run on a new week, drift, doctor, agent fallback, recompile">
<title>Amber demo</title>
<style>{style}</style>
<defs><clipPath id="screen"><rect x="0" y="{top}" width="{width}" height="{height - top}"/></clipPath></defs>
<rect width="{width}" height="{height}" rx="12" fill="{BG}"/>
<rect width="{width}" height="{top - 8}" rx="12" fill="#181825"/>
<rect y="{top - 20}" width="{width}" height="12" fill="#181825"/>
<circle cx="22" cy="18" r="6" fill="#F38BA8"/><circle cx="42" cy="18" r="6" fill="#F9E2AF"/><circle cx="62" cy="18" r="6" fill="#A6E3A1"/>
<text x="{width / 2:.0f}" y="23" text-anchor="middle" fill="#7F849C" style="font:12.5px -apple-system,'Segoe UI',Helvetica,Arial,sans-serif">amber · weekly-report · real session, waits shortened</text>
<g clip-path="url(#screen)"><g class="scroll" xml:space="preserve">
{chr(10).join(body)}
</g></g>
</svg>
"""
    out = SVG if freeze_at is None else SVG.with_name(f"demo-frame-{freeze_at:.0f}.svg")
    out.write_text(svg, encoding="utf-8", newline="\n")
    print(
        f"wrote {out.relative_to(REPO)} ({len(svg) // 1024} KB, {duration:.0f} s loop, {len(lines)} lines)"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record")
    r = sub.add_parser("render")
    r.add_argument("--idle", type=float, default=1.2, help="cap on idle gaps, seconds")
    r.add_argument("--freeze-at", type=float, default=None, help="render a still frame")
    args = parser.parse_args(argv)
    if args.cmd == "record":
        record()
    else:
        render(args.idle, args.freeze_at)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
