# Examples

Three synthetic workflows, each its own Amber project (own `amber.toml`, `.amber/`, `programs/`).
All data comes from seeded generators in `_shared/synth.py`; company, products and people are
fictional ("Northwind Lanterns Co.").

| example | story | drift scenario | status |
|---|---|---|---|
| [weekly-report](weekly-report/) | pull last week's orders, compute RFM shift + top products, post a summary to Slack | `list_orders` returns `amount` instead of `total` → result-shape guard | servers + offline demo ready; live `make demo` needs the `claude` CLI |
| [issue-triage](issue-triage/) | label new GitHub issues, detect duplicates, post a digest | label taxonomy changes (`area:*` → `kind/*`) → `add_labels` errors → error guard | mock server ready; live `make demo` needs the `claude` CLI |
| [price-watch](price-watch/) | check a static catalog site for price/stock changes and alert | prices change and `.price` → `.cost` → extraction drift | site + scenarios ready; live `make demo` needs `claude` and Node ≥ 18 (`@playwright/mcp`) |

## weekly-report

```bash
cd examples/weekly-report
make demo-offline   # seed the synthetic recordings, compile (recorded model fills), verify 3/3, dry-run
make demo           # LIVE: 3 `claude -p` recordings, align, compile, verify, run (needs claude logged in)
make drift          # scenario B: list_orders returns `amount` → `amber run` stops on a guard (exit 4)
make undrift
```

Expected `make demo-offline` tail (synthetic values):

```
│ t1    │   5/5 │         1 │ PASS   │   3/3 PASS
  #1   shop.week_range              ok           46 ms
  #2   shop.list_orders             ok           14 ms
  #3   shop.top_products            ok            0 ms
  #4   shop.rfm_snapshot            ok           14 ms
  #5   llm:step4                    llm           0 ms  1584+85 tok
  #5   slack.post_message           dry-run       0 ms  write effect; pass --allow-writes to execute
Run weekly-report v1 → ok  (r_…, dry-run)
  steps 5 · llm calls 1 · tokens in 1,584 · out 85 · 1.9 s · agent turns 0
```

Servers: `server_shop.py` (in-memory SQLite, 12 weeks / 900 orders / 120 customers / 14 products)
and `_shared/server_slack.py` (posts go to `.outbox/slack.jsonl`). Both use the high-level
`mcp.server.MCPServer` API with `ToolAnnotations(read_only_hint=...)` so Amber knows which tools
write.

## issue-triage

```bash
cd examples/issue-triage
uv run python scenario.py      # 40 synthetic issues, 6 areas, 4 duplicate pairs
make demo                      # LIVE: 3 recordings with --input since=2026-10-27|28|29, align, compile, verify, run
make drift                     # scenario B: taxonomy becomes kind/*; add_labels rejects area:* → `amber run` stops (error guard)
```

## price-watch

```bash
cd examples/price-watch
make serve                     # terminal 1: http://localhost:8765/ (six fictional lanterns)
make demo                      # terminal 2 (LIVE): records with @playwright/mcp, compiles, verifies, runs
make drift                     # scenario B: three prices change and the `.price` span becomes `.cost`
make heal                      # run --fallback agent → compile --refresh → verify
```

The price-watch story is the "self-healing" demo from `docs/06-examples.md`: after `make drift`
the compiled extraction step no longer finds `.price`, the guard stops the run, `amber doctor`
recommends a fallback, and the recompiled v2 uses the new selector.
