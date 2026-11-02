---
title: "Amber — vault index"
tags: [amber/meta, moc]
created: 2026-11-02
---
# Amber — vault index

> [!NOTE]
> This repository doubles as an **Obsidian vault**: open the repo root in Obsidian. Links are
> plain Markdown (GitHub-compatible), every spec has YAML frontmatter (`tags`, `phases`),
> and per-machine workspace state is gitignored (`.obsidian/workspace*.json`).

## Contract and plan

| note | what it is |
|---|---|
| [CLAUDE.md](../CLAUDE.md) | the working contract: non-negotiables, pinned stack, conventions, definition of done |
| [PLAN.md](../PLAN.md) | phased build plan P0–P10 with tasks and acceptance checks |
| [PROGRESS.md](../PROGRESS.md) | dated build log: decisions taken, acceptance output per phase |
| [README-FIRST](README-FIRST.md) | how this plan package is meant to be used (TR/EN) |

## Specifications

| # | note | phases |
|---|---|---|
| 00 | [Vision and positioning](00-vision.md) | — |
| 01 | [Architecture](01-architecture.md) | P0, P1, P6 |
| 02 | [Canonical trace format and adapters](02-trace-format.md) | P1, P2 |
| 03 | [CLI specification](03-cli-spec.md) | P0–P7 |
| 04 | [Compiler: align → codegen → holes → synthesize → verify](04-compiler.md) | P3, P4, P5 |
| 05 | [Runtime: run, guards, ledger, doctor, fallback, recompile](05-runtime.md) | P5, P6, P7 |
| 06 | [Example workflows (all synthetic)](06-examples.md) | P8 |
| 07 | [Web panel](07-panel.md) | P9 |
| 08 | [Testing strategy](08-testing.md) | all |
| 09 | [Benchmarks, README, GIF, release](09-launch.md) | P10 |
| 10 | [Research notes and related work](10-research.md) | P10 |
| 11 | [Decisions (ADR-style)](11-decisions.md) | P0 |

## User docs

| note | what it is |
|---|---|
| [Quickstart](user/quickstart.md) | the 6-command story with expected output |
| [Concepts](user/concepts.md) | traces, skeletons, holes, programs, guards, doctor, fallback |
| [CLI reference](user/cli.md) | every command and flag (generated from the typer app) |
| [Adapters](user/adapters.md) | recording from Claude Code, transcripts, the Agent SDK, any MCP harness |
| [Writing MCP servers Amber can compile](user/servers.md) | annotations, shapes, scenarios |
| [FAQ](user/faq.md) | cache? API key? side effects? editing program.py? |

## Pipeline at a glance

```
record ──► align ──► compile ──► verify ──► run ──► doctor ──► fallback ──► (recompile)
```

- **record** → [02](02-trace-format.md) · **align / compile / verify** → [04](04-compiler.md)
- **run / doctor / fallback** → [05](05-runtime.md) · **examples** → [06](06-examples.md)

## Tags

- `#amber/spec` — specification notes · `#amber/meta` — plan/vault notes · `#amber/log` — progress entries
