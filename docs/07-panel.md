---
title: "07 — Web panel (`amber ui`)"
tags: [amber/spec, ui]
phases: [P9]
status: spec
created: 2026-11-02
---
# 07 — Web panel (`amber ui`)

Scope: read-mostly local dashboard over `.amber/amber.db` and `programs/`. No auth (binds
127.0.0.1). FastAPI + Jinja2 + vendored `htmx.min.js`; no build step, no CDN.

## Routes

| route | content |
|---|---|
| `GET /` | overview: per workflow — latest version, usable, runs (7d), stop rate, tokens saved vs agent baseline (median agent `total_input` from source traces × runs − compiled llm tokens) |
| `GET /programs` · `GET /programs/{wf}` · `GET /programs/{wf}/v{n}` | versions, source traces, step table by kind, manifest, REPORT.md rendered, `program.py` with syntax highlighting (pygments) |
| `GET /runs?workflow=` · `GET /runs/{id}` | run list; detail with step timeline (tool, args preview, result digest, ms, status), guard events, llm calls (model, tokens, valid, confidence), decision records |
| `GET /doctor/{wf}` · `POST /doctor/{wf}` | last diagnosis; POST runs doctor and returns the fragment (htmx swap) |
| `GET /decisions` | ledger table with filters |
| `GET /traces/{id}` | trace viewer (events) |
| `GET /api/*` | JSON twins of each page (reuse Pydantic models) |

## Charts (server-rendered SVG, `ui/charts.py`)

- **Tokens per run**: grouped bars, series `agent baseline` (median from source traces,
  dashed reference line) vs `compiled` (per run). Bars ≤ 24 px wide, 4 px radius on the data
  end only, hairline grid (`currentColor` at 12 % opacity), axis labels 12 px.
- **Guard events over time**: stacked bars by severity.
- **Savings**: a stat tile row (runs, llm tokens, estimated agent tokens avoided, cost avoided
  if cost known) — tiles share one typographic scale.
- Palette (light / dark): series 1 `#2a78d6` / `#3987e5`, series 2 `#eb6834` / `#d95926`,
  series 3 `#1baf7a` / `#199e70`, series 4 `#eda100` / `#c98500`. Soft guard = series 4, hard =
  series 2. Use `prefers-color-scheme` to switch; never rely on color alone (labels/patterns).
- Every chart has a table twin (`<details>` below it) and `<title>`/`<desc>` for
  accessibility.

## Template conventions

`ui/templates/base.html` (nav, theme vars), partials per table, fragments for htmx swaps
(`_doctor_result.html`). Keep CSS in one file (`static/app.css`, ≤ 300 lines), system font
stack, 8-px spacing grid.

## Tests

`tests/unit/ui/test_routes.py` with a fixture DB (built from the trace fixtures + one fake
run): every route returns 200 and contains the workflow name; `/api/runs` validates against
the Pydantic model; chart function returns valid SVG (parse with `xml.etree`).
