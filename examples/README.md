# Examples

Three synthetic workflows, each its own Amber project (own `amber.toml`, `.amber/`, `programs/`).
All data comes from seeded generators in `_shared/synth.py` and each example's `scenario.py`;
company, products and people are fictional ("Northwind Lanterns Co."). All three were recorded,
compiled, verified and run live on 2026-11-02 (see `bench/results.md`).

| example | story | drift scenario | what happened live |
|---|---|---|---|
| [weekly-report](weekly-report/) | pull last week's orders, compute RFM shift + top products, post a summary to Slack | `list_orders` returns `amount` instead of `total` → result-shape guard | full self-healing loop: guard stop → doctor `fallback` → agent resumes → `compile --refresh` → v2 verifies 4/4 |
| [issue-triage](issue-triage/) | label new GitHub issues, detect duplicates, post a digest | label taxonomy changes (`area:*` → `kind/*`) → `add_labels` errors → error guard | labels and duplicate comments compiled to deterministic transforms; one `llm()` call for the digest |
| [price-watch](price-watch/) | check a static catalog site for price/stock changes and alert | prices change and `.price` → `.cost` | the drift is absorbed: the program reads Playwright's accessibility snapshot, which has no CSS classes, and alerts correctly |

Every `make record` passes `--model claude-sonnet-5-5` (override with `AGENT_MODEL=...`) so the
agent never runs on your default model, and `--setting-sources user` so this repository's
`CLAUDE.md` stays out of the agent's context.

## weekly-report

```bash
cd examples/weekly-report
make demo-offline   # seed the synthetic recordings, compile (recorded model fills), verify 3/3, dry-run
make demo           # LIVE: 3 `claude -p` recordings, align, compile, verify, run (needs claude logged in)
make drift          # scenario B: list_orders returns `amount` → `amber run` stops on a guard (exit 4)
make heal           # run --fallback agent → compile --refresh → verify
make undrift
```

Servers: `server_shop.py` (in-memory SQLite, 12 weeks / 900 orders / 120 customers / 14 products,
the last week is 2026-W44) and `_shared/server_slack.py` (posts go to `.outbox/slack.jsonl`).
Both use the high-level `mcp.server.MCPServer` API with `ToolAnnotations(read_only_hint=...)` so
Amber knows which tools write. `docs/user/quickstart.md` shows the real output of every step.

## issue-triage

```bash
cd examples/issue-triage
uv run python scenario.py      # 40 synthetic issues, 6 areas, 4 duplicate pairs (duplicates are newer)
make demo                      # LIVE: 3 recordings with --input since=2026-10-27|29|30, then run on 10-28
make drift                     # scenario B: taxonomy becomes kind/*; add_labels rejects area:* → error guard
```

The writes are batches (`add_labels(updates)`, `add_comments(comments)`): Amber v0.1 does not
collapse per-item loops, and one batch write per run compiles to one step whose list argument a
single hole fills. The three recorded days have different duplicate sets, so the comments are
derived rather than baked in as a constant.

## price-watch

```bash
cd examples/price-watch
make serve                     # terminal 1: http://localhost:8765/ (six fictional lanterns)
make demo                      # terminal 2 (LIVE): records on day A and update day A2, compiles, verifies, runs
make drift                     # scenario B: three prices change and the `.price` span becomes `.cost`
```

The recordings cover a quiet day (scenario A, nothing to alert) and a price-update day (A2, two
prices move), so the compiled program learns both outcomes: an LLM gate decides whether to post,
a judgment writes the alert. The task names the browser tools, and the recordings disallow
`Read`, `Glob` and `Grep`. Otherwise the agent reads @playwright/mcp's timestamped snapshot files
from disk, or fetches `previous.json` from page JavaScript in one run and navigates to it in
another, which compiles to steps that cannot be relied on.

`docs/06-examples.md` planned price-watch as the self-healing demo: renaming `.price` was meant to
break a selector-based extraction step. Playwright MCP's snapshot is an accessibility tree without
CSS classes and `browser_evaluate` returns markdown text, so nothing in the compiled program
depends on the class name. Under scenario B the program posted the right alert (three price
changes and a product that went out of stock) without a guard stop. The self-healing loop is
demonstrated on weekly-report instead.
