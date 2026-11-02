---
title: "Quickstart"
tags: [amber/user]
---
# Quickstart — the 60-second story

Everything below runs inside `examples/weekly-report`, a synthetic project (mock shop and Slack
MCP servers, fictional data). You need Python 3.12+, [uv](https://docs.astral.sh/uv/) and
either a logged-in `claude` CLI or `ANTHROPIC_API_KEY`.

```bash
uv tool install amber-compiler        # or: pipx install amber-compiler (needs a release on PyPI)
git clone https://github.com/enestanerr/Amber && cd Amber/examples/weekly-report
```

## 1. Record the agent a few times

```bash
amber record weekly-report --input week=2026-W42 --mcp mcp.json --allow 'mcp__shop__*' --allow 'mcp__slack__*' \
  -- claude -p "$(sed 's/{week}/2026-W42/' task.md)"
amber record weekly-report --input week=2026-W43 ...   # same, other inputs
amber record weekly-report --input week=2026-W44 ...
```

```
Recorded weekly-report → t_20261102_165648_bc39fe  (success)
  tool calls      6   (builtin 1 · shop 4 · slack 1)
  judgment cands  1   (slack.post_message.text)
  tokens          in 18,122 (cached 12,000) · out 966 · $0.034 · 19.5 s
Next: record 1-2 more runs with different inputs, then `amber align weekly-report --show`
```

Different inputs matter: identical runs give the compiler nothing to generalise from.

## 2. Look at the skeleton

```bash
amber align weekly-report --show
```

```
Skeleton weekly-report  (3 traces · 5 steps · 1 judgment · 1 transform-needed · 1 pruned)
#  tool                 kind                        provenance                                  in
1  shop.week_range      deterministic               week ← Input(week)                          3/3
2  shop.list_orders     deterministic · observe     since ← Ref(0,$.start) · until ← Ref(0,$.end) 3/3
3  shop.top_products    deterministic · observe     n ← Const(5) · since ← Ref(0,$.start) · …   3/3
4  shop.rfm_snapshot    transform-needed · observe  as_of ← Unknown(transform)                  3/3
5  slack.post_message   judgment                    channel ← Const("#eng") · text ← Unknown(judgment)
-  builtin.Read (pruned)                            read-only, unreferenced, present 1/3
```

## 3. Compile (and verify)

```bash
amber compile weekly-report
```

```
 hole             kind       step  attempts  result
 transform_step3  transform     4         1  accepted
 judgment_step4   judgment      5         1  accepted
  holes filled 2/2 · usable: yes
│ t1 │ 5/5 │ 1 │ PASS │  │ t2 │ 5/5 │ 1 │ PASS │  │ t3 │ 5/5 │ 1 │ PASS │   3/3 PASS
```

`programs/weekly-report/v1/` now holds `program.py`, `manifest.json`, `tests/test_replay.py`
and `REPORT.md`. The program is ordinary Python — read it before running it live.

## 4. Verify any time

```bash
amber verify weekly-report            # replays every source trace; exit 3 on divergence
```

## 5. Run it live, cheaply

```bash
amber run weekly-report --input week=2026-W45 --allow-writes
```

```
  #1   shop.week_range              ok           46 ms
  #2   shop.list_orders             ok           14 ms
  #3   shop.top_products            ok            0 ms
  #4   shop.rfm_snapshot            ok           14 ms
  #5   llm:step4                    llm         820 ms  1584+85 tok
  #5   slack.post_message           ok          180 ms
Run weekly-report v1 → ok  (r_20261102_180004_418672, live)
  steps 5 · llm calls 1 · tokens in 1,584 · out 85 · 1.9 s · agent turns 0
```

Without `--allow-writes`, write-effect tools are logged as `dry-run` and return a placeholder
shaped like the recording.

## 6. When the world drifts

```bash
make drift                             # the shop now returns `amount` instead of `total`
amber run weekly-report --input week=2026-W45 --allow-writes   # exit 4: result_shape guard
amber doctor weekly-report             # degraded → recommendation: fallback (exit 5)
amber run weekly-report --input week=2026-W45 --allow-writes --fallback agent
amber compile weekly-report --refresh  # v2 verifies against all traces incl. the fallback one
```

## Offline variant

No `claude` at hand? `make demo-offline` in `examples/weekly-report` seeds the synthetic fixture
recordings, compiles with recorded model fills, verifies 3/3 and dry-runs against the mock
servers. It exercises everything except the live model calls.
