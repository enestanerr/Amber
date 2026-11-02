---
title: "10 — Research notes and related work"
tags: [amber/spec, research]
phases: [P10]
status: spec
created: 2026-11-02
---
# 10 — Research notes and related work

Collected November 2026 while scoping the project. Use these for README "Why" and "Related
work" sections. **Re-verify every link and number before quoting it publicly**; cite the
source next to the number. Never present a paper's numbers as Amber's.

## Primary evidence: compiling trajectories works

**FlashScript** — arXiv 2610.33876 (24 Oct 2026; authors include Sumit Gulwani and Ashish
Tiwari). Compiles agent trajectories into *neuro-symbolic programs* (deterministic code +
LLM calls for judgment). Reported on 120 WorkBench tasks with Claude Haiku 4.5: ~70× fewer
input tokens, ~1/3 runtime, identical tool calls on every run; a skill-guided agent fell to
23 % on composite tasks while the compiled programs stayed correct; programs generalized to
108 unseen instances. **No code release.** → Amber is an open implementation of the idea with
a different engineering emphasis (harness-agnostic capture, replay tests, drift loop).

**Amazon tool-making** — arXiv 2607.08010 (Jul 2026): agents that author reusable tools from
their own runs; p50 latency −42 %; baseline 33.7 turns per alarm; a 44-tool library generated
once for ~800K tokens. Supports the "pay once, run many" economics.

**AWO meta-tools** — arXiv 2601.22037 (Jan 2026): workflow optimization via meta-tools; same
direction, orchestration-level.

**SkillsBench** (2026): self-generated skills give negligible gains; curated ones help →
argues for *verified* artifacts (Amber's replay tests) over free-form skill files.

## Market context (for the "why now")

- LangChain *State of Agent Engineering 2026*: 57 % have agents in production; quality is the
  top barrier (32 %), latency second (20 %); observability adoption 89 % vs evals 52 %.
- Kore.ai 2026 survey: 72 % report unmanaged agent risk; 8 of 10 have paid for consequential
  agent actions.
- Widely cited figure: ~88 % of agent prototypes never reach production.
- GitHub Octoverse 2026: 40 %+ of PRs started by agents (Sept 2026) vs 17 % in March;
  agent-config repos at 8× commits; TypeScript #1 language; awesome-mcp-servers among the
  fastest-growing repos by contributors.

## Related work and how Amber differs

| project | what it does | status (Nov 2026) | difference |
|---|---|---|---|
| **muscle-mem** (pig-dot-dev) | behaviour cache: wraps *your* agent with decorators, records tool-call trajectories, replays a recorded path when environment `Check`s pass; later added parameterization | ~750 stars, last push Jun 2025, inactive | Amber compiles *multiple* runs into a parameterized program with typed `llm()` judgment steps; captures from Claude Code / MCP without instrumenting your agent; generates replay tests; doctor/fallback/recompile loop. muscle-mem replays one path; Amber generalizes. |
| **FlashScript** | research prototype (above) | paper only | open source, adapters, operating loop |
| **taprun** | records browser workflows and replays them | closed-source, paid, browser-only | any MCP tool, code output you own |
| **Codex "Record and Replay"** (Jun 2026) | drafts a SKILL.md from a session | skill text, not code | executable program + tests + guards |
| **Claude Code skills / hooks** | reusable instructions / lifecycle hooks | — | complementary: a skill tells the agent how; Amber removes the agent from the loop for the repeatable part |
| **VCR-style cassette libs** (vcrpy, pytest-recording) | record/replay HTTP for tests | mature | Amber's cassette is the same idea applied to MCP tool calls, plus volatility masking and llm() recording |
| **Workflow engines** (Temporal, Prefect, n8n) | run programs reliably | mature | Amber *produces* the program; its output can be scheduled by any of them |
| **Observability** (LangSmith, Langfuse, OpenTelemetry GenAI) | trace agents | mature | input source for a future adapter (OTel spans → Amber trace) |

## Positioning sentence (README)

"Caches replay the past; skills instruct the agent; Amber compiles what the agent learned
into a program you can read, test and run — and hands control back to the agent only when
the world changes."

## Open research questions Amber can contribute to (good issues / posts)

- Loop and pagination detection from repeated calls (v0.2).
- Minimal sufficient context for judgment steps (what to include in `llm()` context).
- Drift taxonomy: which schema/result changes are safely absorbable vs require fallback.
- Cross-harness alignment (same workflow recorded with two different agents).
