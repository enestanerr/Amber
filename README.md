# Amber — compile your agent's runs into programs

> **Under construction.** Amber is being built phase by phase from [PLAN.md](PLAN.md); this
> README only lists what exists and has a test. See [PROGRESS.md](PROGRESS.md) for the log.

Amber is an open-source **compiler for agent runs**. You record an AI agent doing a workflow a
few times (Claude Code, the Claude Agent SDK, or any MCP-speaking harness), and Amber compiles
those runs into a **neuro-symbolic program**: deterministic Python for the parts that are
mechanical, small schema-validated `llm()` calls for the parts that need judgment, plus
**replay tests** generated from the recordings. The program then runs on a schedule at a
fraction of the tokens, with guards, a `doctor` that detects drift, and a fallback that hands
control back to the agent and recompiles.

## Status

| phase | state |
|---|---|
| P0 Bootstrap — packaging, CLI skeleton (`--version`, `init`), lint/type/test/CI | done |
| P1 Trace model + store — `Trace` models, canonicalization, JSON+SQLite store, `amber traces` | done |
| P2 Recorders — `amber record` (claude -p stream-json, `--from-stdin`, `--sdk`), `amber import` (transcripts, proxy fragments), `amber proxy` | done |
| P3 Align — sequence alignment, provenance, classification, pruning, `amber align --show` | done |
| P4 Compile — codegen, typed holes + synthesizer, sandbox/lint, versions, `amber compile`, `amber programs` | done |
| P5–P10 | not started |

## Install (development)

```bash
git clone https://github.com/enestanerr/Amber && cd Amber
uv sync --all-extras
uv run amber --version      # amber 0.1.0a0
uv run amber init           # creates .amber/ and amber.toml
```

## Documentation

The `docs/` folder is the specification and doubles as an Obsidian vault: open the repository
root in Obsidian and start at [docs/index.md](docs/index.md).

## License

Apache-2.0 — see [LICENSE](LICENSE).
