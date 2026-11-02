---
title: "Amber — Claude Code plan paketi / plan package"
tags: [amber/meta]
status: spec
created: 2026-11-02
---
# Amber — Claude Code plan paketi / plan package

**TR — nasıl kullanılır**

1. Boş bir repo aç: `mkdir amber && cd amber && git init`.
2. Bu paketteki `CLAUDE.md`, `PLAN.md` ve `docs/` klasörünü repo köküne kopyala.
3. Claude Code'u repo kökünde başlat ve aşağıdaki **başlangıç mesajını** yapıştır.
4. Claude Code `PLAN.md`'deki fazları sırayla uygular; her fazın kabul komutlarını çalıştırıp
   çıktıyı `PROGRESS.md`'ye yazar. Sana yalnızca `docs/11-decisions.md`'de **ASK** işaretli
   kararı (isim) sorar.
5. Canlı adımlar (kayıt, bench, GIF) için `claude` CLI'nin oturum açmış olması ya da
   `ANTHROPIC_API_KEY` yeterli. Price-watch örneği için Node ≥ 18 (Playwright MCP) gerekir.

**EN — how to use**

Copy `CLAUDE.md`, `PLAN.md` and `docs/` into an empty repository, start Claude Code there and
paste the kickoff prompt below. Claude Code works phase by phase and records acceptance
output in `PROGRESS.md`.

---

## Kickoff prompt (paste into Claude Code)

```
Read CLAUDE.md, PLAN.md and every file in docs/ before writing any code. This repository is
empty apart from those files; you are building Amber v0.1 from this plan.

Rules of engagement:
- Work phase by phase in PLAN.md order (P0 → P10). Do not start a phase before the previous
  phase's acceptance commands pass. Run the acceptance commands exactly as written and paste
  the key output lines into PROGRESS.md under a dated heading for the phase.
- Take every default in docs/11-decisions.md and note it in PROGRESS.md. The only question you
  may ask me now is D1 (name/package name) — ask it with the facts from that file, then wait
  for my answer before P0.
- Before P2, verify the installed API surfaces: `uv run python -c "import mcp, anthropic; print(mcp.__version__, anthropic.__version__)"`
  and `claude --help`. If a spec snippet in docs/ does not match the installed version, fix the
  doc with a short note and continue; do not silently diverge.
- Write tests first where the spec gives exact behaviour (trace mapping, canonicalization,
  alignment, cassette replay). Unit tests never touch the network.
- You may use parallel subagents only for the work PLAN.md marks as parallel-safe (P8 mock
  servers/generators, P9 panel after the ledger schema is frozen, user docs). The critical
  path P1 → P7 is sequential.
- Commit after every completed task group with Conventional Commits. Never commit .amber/,
  secrets, or anything that is not synthetic.
- When all phases are done, run `make demo`, `make drift`/`make heal` on price-watch and
  `bench/run_bench.py --all --runs 3`, update README with the measured table, and summarize
  in PROGRESS.md what is done, what is deferred, and the exact commands to reproduce the demo.

Start by confirming you have read the files (one paragraph: what Amber is, the critical path,
and the first three things you will do in P0), then ask the D1 question.
```

## Paket içeriği / contents

```
CLAUDE.md                 project contract for Claude Code (rules, stack, conventions, definition of done)
PLAN.md                   phased plan P0–P10 with tasks and acceptance checks
docs/00-vision.md         positioning, principles, non-goals, success metrics
docs/01-architecture.md   modules, data flow, storage, SQLite schema, security posture
docs/02-trace-format.md   canonical trace schema; adapters: claude stream-json, transcripts, Agent SDK, MCP proxy
docs/03-cli-spec.md       every command, flag, output and exit code
docs/04-compiler.md       align → codegen → holes → synthesizer → verify (the core)
docs/05-runtime.md        live backends, builtins policy, guards, ledger, doctor, fallback policy, recompile
docs/06-examples.md       three synthetic example workflows and their mock MCP servers
docs/07-panel.md          web panel spec
docs/08-testing.md        test strategy and must-have cases per phase
docs/09-launch.md         benchmark protocol, README skeleton, GIF script, release checklist
docs/10-research.md       evidence and related work (FlashScript, muscle-mem, taprun, Codex, …)
docs/11-decisions.md      ADRs with defaults; D1 (name) is the only ASK
```
