---
title: "11 — Decisions (ADR-style)"
tags: [amber/spec, adr]
phases: [P0]
status: spec
created: 2026-11-02
---
# 11 — Decisions (ADR-style)

Each decision has a default. Only items marked **ASK** need the user before proceeding; all
others are taken and noted in `PROGRESS.md`.

## D1 — Name and package name  **ASK (once, at P0)**

Facts (checked 2 Nov 2026): PyPI `amber` is taken by an abandoned 0.0.1 placeholder from
years ago; `amber-compiler`, `amberc`, `amber-agent` are free. GitHub has `amber-lang/amber`
(~4.7k stars, "a programming language compiled to Bash") — a brand collision with another
*compiler* named Amber.

Default: project **Amber**, distribution `amber-compiler`, import `amber`, CLI `amber`.
Install: `uv tool install amber-compiler`. Alternative if the user wants a collision-free
brand: pick one of the free names (`rotec`, `byrote`, `hardcode` were free on PyPI on the same
date; verify again) and rename at P0 before any code exists. Ask the user with these facts;
proceed with the default if no answer.

## D2 — License
Apache-2.0 (patent grant matters for a compiler that generates code companies will run).
Alternative MIT. Default taken.

## D3 — Synthesis path: model API vs Agent SDK
Default: `anthropic` Messages API with structured outputs (`messages.parse`) for hole
filling and runtime `llm()`; `claude` CLI backend as a fallback for users without an API key.
The Agent SDK is only used as an optional *recorder*. Rationale: compile must be
deterministic and testable; structured outputs give schema-valid JSON; no CLI dependency
for compile.

## D4 — Hybrid codegen (deterministic skeleton + typed holes) vs full LLM synthesis
Hybrid. Rationale in `CLAUDE.md` non-negotiable #2 and `04-compiler.md`.

## D5 — Default model IDs
Resolve at `amber init` via `anthropic.models.list()`: synthesis = newest Sonnet-class model,
judgment = newest Haiku-class model; write explicit IDs into `amber.toml`. Documented
defaults if unresolvable: `claude-sonnet-5-5` and `claude-haiku-5-5` (verify; IDs change).
Never hardcode model IDs outside config.

## D6 — First adapters
`claude-stream` (wrapping `claude -p`) first, `claude-transcript` second, `mcp-proxy` third,
`agent-sdk` last (optional extra). OpenAI Agents SDK and OpenTelemetry in v0.2.

## D7 — Storage
SQLite (stdlib) + JSON files. No ORM, no Postgres. Rationale: zero-setup local tool.

## D8 — Write-effect default
Dry-run by default; `--allow-writes` explicit. Rationale: compiled programs run unattended.

## D9 — Examples
Three synthetic workflows (weekly-report, issue-triage, price-watch). All data from seeded
generators in-repo. No real APIs in the demo path (Playwright MCP talks to a local static
site).

## D10 — Panel stack
FastAPI + Jinja2 + HTMX, server-rendered SVG charts. No Node build in the Python package.

## D11 — Scheduler
Minimal in-process cron (P2 priority). Recommend system cron / GitHub Actions in docs.

## D12 — Python version
3.12 minimum (modern typing, `tomllib`), test 3.13.

## D13 — Program output language
Python only in v0.1. TypeScript output is a roadmap item.

## D14 — What "success" means for a trace
`result.subtype == "success"` and `is_error == false` (stream); imports default to
`unknown` unless `--status` given; align uses successes only unless `--include-failed`.

## D15 — Public claims
Only measured numbers from `bench/`; research numbers always cited. (CLAUDE.md #5.)

## D16 — Content/documentation language
Code, docs, README in English. The maintainer may publish Turkish summaries separately.
