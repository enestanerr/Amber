---
title: "00 — Vision and positioning"
tags: [amber/spec, vision]
status: spec
created: 2026-11-02
---
# 00 — Vision and positioning

## One sentence

**Amber turns an agent's successful runs into a program**: deterministic code for the
mechanical parts, small schema-checked `llm()` calls for the judgment parts, replay tests
from the recordings, and an operating loop (guards → doctor → fallback → recompile) so the
program stays correct when the world drifts.

## Why this exists

- Agents are great at *figuring out* a workflow and terrible at *repeating* it: every run
  re-spends thousands of tokens re-deriving the same tool calls, with nondeterministic
  results. Teams report quality and latency as top barriers to production, and most agent
  prototypes never ship.
- Research shows the compile-the-trajectory idea works: FlashScript (arXiv 2610.33876,
  Oct 2026) compiled agent trajectories into neuro-symbolic programs that reproduced the
  agent's tool calls with ~70× fewer input tokens and ~1/3 runtime, stayed correct where a
  skill-guided agent degraded, and generalized to unseen instances. No code was released.
- Everything else in the space either caches exact trajectories (muscle-mem: replay one
  recorded path when environment checks pass, you instrument your own agent, inactive since
  2025), stays inside the browser (taprun: closed, paid), or stops at drafting a SKILL.md
  (Codex "record and replay"). Nobody ships an open, harness-agnostic **compiler** with
  replay tests and a drift loop.

## Who it is for

1. Developers running Claude Code / Agent SDK workflows on a schedule (reports, triage,
   monitoring) who want them cheap, fast and deterministic.
2. Teams putting agents in production who need an auditable artifact (a Python file with
   tests) instead of a prompt and a prayer.
3. Researchers who want an open implementation of trajectory → program compilation.

## Product principles

- **Show the program.** The output is a readable `program.py` a human can review, diff and
  edit. No opaque cache.
- **The compiler is boring on purpose.** Structure comes from alignment and codegen; the
  model fills typed holes and every fill is checked against recordings.
- **Honest about judgment.** Steps that need language understanding stay `llm()` calls with
  explicit schemas and minimal context. Amber does not pretend everything is deterministic.
- **Fail loudly, recover automatically.** Guards stop bad runs; doctor explains; fallback
  returns control to the agent; recompile learns from it.
- **Harness-agnostic capture.** Claude first (stream-json, transcripts, Agent SDK), MCP proxy
  for everyone else, more adapters later.

## Non-goals (v0.1)

Replacing agent frameworks; multi-agent orchestration; a hosted platform; UI for editing
programs; supporting every Claude Code builtin tool (only a safe subset).

## Success metrics for the launch

- `make demo` works first try on a clean machine with the `claude` CLI (≤ 10 minutes).
- Bench table shows an order-of-magnitude token reduction on all three examples, measured.
- 1,000 GitHub stars within 60 days of launch; ≥ 3 external contributors; ≥ 1 adapter PR.
