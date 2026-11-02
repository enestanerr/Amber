---
title: "Quickstart"
tags: [amber/user]
---
# Quickstart — the 60-second story

Everything below runs inside `examples/weekly-report`, a synthetic project (mock shop and Slack
MCP servers, fictional data). You need Python 3.12+, [uv](https://docs.astral.sh/uv/) and either
a logged-in `claude` CLI (a claude.ai subscription is enough, no API key) or `ANTHROPIC_API_KEY`.
The outputs shown are from a live session on 2026-11-02 (Windows 11, claude CLI 2.1.316, agent
`claude-sonnet-5-5`, judgments `claude-haiku-5-5`), trimmed.

```bash
git clone https://github.com/enestanerr/Amber && cd Amber && uv sync --all-extras
cd examples/weekly-report
```

## 1. Record the agent a few times

`make record` runs the agent three times with different inputs. Per run it is:

```bash
amber record weekly-report --input week=2026-W42 --mcp mcp.json \
  --allow 'mcp__shop__*' --allow 'mcp__slack__*' --permission-mode acceptEdits \
  -- claude -p "$(sed 's/{week}/2026-W42/' task.md)" --model claude-sonnet-5-5 --setting-sources user
```

```
Recorded weekly-report → t_20261102_193532_f72fc9  (success)
  tool calls      8   (shop 6 · slack 2)
  judgment cands  1   (slack.post_message.text)
  tokens          in 169,396 (cached 142,241) · out 4,680 · $0.170 · 28.9 s
```

Pass `--model` explicitly so recordings never run on your default (possibly most expensive)
model; the fallback relaunches the agent with the same flags. `--setting-sources user` keeps the
repository's own `CLAUDE.md` out of the agent's context, and `amber record --mcp` adds
`--strict-mcp-config` so your account-level MCP connectors stay out too. Different inputs
matter: identical runs give the compiler nothing to generalise from.

## 2. Look at the skeleton

```bash
amber align weekly-report --show
```

```
Skeleton weekly-report  (3 traces · 7 steps · 1 judgment · 1 transform-needed · 2 pruned)
1  shop.week_range     deterministic            week ← Input(week)                            3/3
2  shop.week_range     transform-needed         week ← Unknown(transform)                     3/3
3  shop.list_orders    deterministic · observe  since ← Ref(0,$.start) · until ← Ref(0,$.end) 3/3
4  shop.rfm_snapshot   deterministic · observe  as_of ← Ref(0,$.end)                          3/3
5  shop.rfm_snapshot   deterministic · observe  as_of ← Ref(1,$.end)                          3/3
6  shop.top_products   deterministic · observe  n ← Const(5) · since ← Ref(0,$.start) · …    3/3
7  slack.post_message  judgment                 channel ← Unknown(judgment) · text ← Unknown(judgment)
-  slack.list_channels (pruned)                 read-only, unreferenced, present 2/3
```

Step 2 is the previous ISO week (2026-W41 for input 2026-W42): a transform hole. The agent looked
up the channel list in some runs; nothing used the result, so it was pruned.

## 3. Compile (and verify)

```bash
amber compile weekly-report
```

```
 hole             kind       step  attempts  result
 transform_step1  transform     2         1  accepted
 judgment_step6   judgment      7         1  accepted
  holes filled 2/2 · usable: yes
```

`programs/weekly-report/v1/` now holds `program.py`, `manifest.json`, `tests/test_replay.py` and
`REPORT.md`. The program is ordinary Python — read it before running it live.

## 4. Verify any time

```bash
amber verify weekly-report            # replays every source trace; exit 3 on divergence
```

```
│ t_20261102_193532_f72fc9 │ 7/7 │ 1 │ PASS │
│ t_20261102_193603_4cb6ac │ 7/7 │ 1 │ PASS │
│ t_20261102_193656_20fb93 │ 7/7 │ 1 │ PASS │
                 3/3 PASS
```

## 5. Run it live, cheaply

```bash
amber run weekly-report --input week=2026-W40 --allow-writes     # a week nobody recorded
```

```
  #7   llm:step6                    llm       28750 ms  21170+8072 tok
  #7   slack.post_message           ok            0 ms
Run weekly-report v1 → ok  (r_20261102_200341_8ead66, live)
  steps 7 · llm calls 1 · tokens in 21,170 · out 8,072 · 31.5 s · agent turns 0
```

The posted summary's segment counts and top products matched a recount from the shop data.
Without `--allow-writes`, write-effect tools are logged as `dry-run` and return a placeholder
shaped like the recording.

## 6. When the world drifts

```bash
make drift                             # the shop now returns `amount` instead of `total`
amber run weekly-report --input week=2026-W41 --allow-writes
#   #3 shop.list_orders  stopped  $[0]: missing key 'total'        → exit 4, 0 llm tokens
amber doctor weekly-report
#   ✗ result_shape  $[0]: missing key 'total' … (hard)  → degraded, recommendation: fallback (exit 5)
amber run weekly-report --input week=2026-W41 --allow-writes --fallback agent
#   decision: fallback_resume — the agent resumes from step 3 with the completed results
#   fallback trace t_20261102_204011_cc43a1 saved (8 calls, 8 agent turns)
amber compile weekly-report --refresh  # → v2
amber verify weekly-report             # 4/4 PASS (three recordings + the fallback trace)
amber doctor weekly-report             # healthy
make undrift
```

## Offline variant

No `claude` at hand? `make demo-offline` in `examples/weekly-report` seeds the synthetic fixture
recordings, compiles with recorded model fills, verifies 3/3 and dry-runs against the mock
servers. It exercises everything except the live model calls.
