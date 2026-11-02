---
title: "Adapters"
tags: [amber/user]
---
# Adapters — where recordings come from

| adapter | command | notes |
|---|---|---|
| `claude-stream` | `amber record <wf> [--input k=v]… [--mcp cfg.json] [--allow PATTERN]… -- claude -p "task"` | wraps the Claude Code CLI; injects `--output-format stream-json --verbose`; the trace is saved even when the run fails (exit 1) |
| `claude-transcript` | `amber import <wf> <session.jsonl>` or `--latest` | reads `~/.claude/projects/<encoded cwd>/*.jsonl`; sidechains and meta lines are skipped, branches resolve to the longest chain, compactions become notes |
| `agent-sdk` | `amber record <wf> --sdk -- claude -p "task"` | runs `claude_agent_sdk.query` in-process (extra `[sdk]`); same mapper as stream-json |
| `mcp-proxy` | host config: `{"command": "amber", "args": ["proxy", "--name", "shop", "--", "uv", "run", "server.py"]}` then `amber import <wf> --proxy .amber/proxy` | harness-agnostic: any MCP client; no thoughts in the trace |

## Recording tips

- Keep the task prompt tight and tool-oriented; say which tools to use.
- Record 2–3 runs with **different inputs**; declare them with `--input` so provenance is exact.
- Prefer MCP tools over Bash/Read for anything the program must repeat; only `Read`, `Write`,
  `Glob`, `Grep`, `WebFetch` and a template-locked `Bash` are supported at run time.
- Pass `--mcp cfg.json`: besides configuring the agent, Amber connects to those servers after the
  run and replaces the stub tool schemas with the real ones, so the manifest can detect schema
  drift later.
- A denied tool call shows up as an error result; the recorder warns and suggests `--allow`.

## What `--json` gives you

Every command prints a machine-readable twin with `--json`: `record`/`import` print the trace,
`align` the skeleton, `compile` the hole outcomes and the verify table, `verify` per-trace
reports, `run` the `RunResult`, `doctor` the diagnosis.
