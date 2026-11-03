# Amber for Claude Code

Turn a workflow Claude Code keeps repeating into a compiled Amber program, and repair it when the
world drifts.

[Amber](https://github.com/enestanerr/Amber) records an agent doing a task a few times and
compiles the recordings into a readable Python program: deterministic code for the mechanical
steps, small schema-validated `llm()` calls where judgment is needed, replay tests generated from
the recordings, and guards on every live call. This plugin adds two skills that walk you through
it.

## Install

In Claude Code (2.1.275 or later):

```text
/plugin install amber --marketplace enestanerr/Amber
```

## Skills

- **`/amber:compile [workflow-name]`** sets up an Amber project, records three runs with
  different inputs, shows you the aligned skeleton, then compiles, verifies, dry-runs and
  schedules the program. It asks before each recording, because a recording is a full agent run
  on your account.
- **`/amber:heal [workflow-name]`** runs `amber doctor` after a run stopped on a guard, explains
  what drifted, and picks the repair: recompile, agent fallback or re-record.

## Requirements

- The `amber` CLI from [PyPI](https://pypi.org/project/amber-compiler/):
  `uv tool install amber-compiler` (Python 3.12+).
- Claude Code logged in with a claude.ai subscription, or `ANTHROPIC_API_KEY` set.

## What the plugin runs and sends

The plugin contains two skills, written as Markdown instructions. It has no hooks, MCP servers,
scripts or binaries. Following the skills, Claude runs the `amber` CLI on your machine, which:

- runs `claude -p` to record agent runs, and calls Claude models through your `claude` login or
  the Anthropic API to fill program holes and to run judgment steps;
- connects to the MCP servers you configure in `amber.toml` and `mcp.json`;
- replays the Claude Code builtins a recording used (reading, writing and searching files in the
  project, fetching web pages, and shell commands that match the recorded template) under the
  policy in [docs/05-runtime.md](https://github.com/enestanerr/Amber/blob/main/docs/05-runtime.md);
- keeps traces, programs and run logs in your project (`.amber/` and `programs/`), and serves a
  local panel on `127.0.0.1` only when you run `amber ui`.

Amber sends no telemetry, and nothing goes to services other than Claude and the servers and
pages your workflow uses. Write tools run in dry-run mode unless you pass `--allow-writes`, and
the skills ask before anything costs tokens or writes.

Documentation, source and issues: [github.com/enestanerr/Amber](https://github.com/enestanerr/Amber).
License: Apache-2.0.
