# weekly-report — program v1

> [!WARNING]
> Review `program.py` before running it live. It is ordinary Python generated from your
> recordings; the model only filled the typed holes listed below, and every fill was checked
> against the recordings, but you are responsible for what runs against your servers.

- compiled: 2026-11-02T18:00:03+00:00
- source traces: t1, t2, t3
- skeleton hash: `54127fb9ffd32aa7` · program hash: `b2ca25d3850f1b81`
- usable: **yes**

## Steps

| # | tool | kind | provenance | in |
|---|---|---|---|---|
| 1 | `shop.week_range` | deterministic | week ← Input(week) | 3/3 |
| 2 | `shop.list_orders` | deterministic · observe | since ← Ref(0,$.start) · until ← Ref(0,$.end) | 3/3 |
| 3 | `shop.top_products` | deterministic · observe | n ← Const(5) · since ← Ref(0,$.start) · until ← Ref(0,$.end) | 3/3 |
| 4 | `shop.rfm_snapshot` | transform-needed · observe | as_of ← Unknown(transform) | 3/3 |
| 5 | `slack.post_message` (write) | judgment | channel ← Const('#eng') · text ← Unknown(judgment) | 3/3 |
| – | `builtin.Read` (pruned) | | read-only, unreferenced, present 1/3 | |


## Holes

### `transform_step3` (transform) — accepted after 1 attempt(s)

In every recording as_of equals the week start from r0 minus one day, so the transform parses r0['start'] and subtracts a day; r1 and r2 are not needed.


### `judgment_step4` (judgment) — accepted after 1 attempt(s)

The recorded texts follow the same five-sentence structure built from the orders, top products and RFM snapshot that are available as context, so the prompt spells out that structure and names only the available context keys.




## Verify

| trace | calls | llm steps | result | divergence |
|---|---|---|---|---|
| t1 | 5/5 | 1 | PASS |  |
| t2 | 5/5 | 1 | PASS |  |
| t3 | 5/5 | 1 | PASS |  |

## Warnings

- pruned: step 4 builtin.Read (read-only, unreferenced, present 1/3)

