"""weekly-report v1 — compiled by amber from 3 recorded run(s).

Skeleton hash 54127fb9ffd32aa7. Deterministic structure; the model only filled the typed holes
marked ``AMBER-HOLE``. Review before running live: this is ordinary Python.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field

from amber.runtime import Context, program


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week: str = Field(description="Declared input", examples=["2026-W42", "2026-W43", "2026-W44"])


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    ts: str
    text: str


# AMBER-HOLE:transform_step3 — transform for shop.rfm_snapshot (as_of)
def transform_step3(inputs: dict, r0, r1, r2) -> dict:
    # the snapshot is taken as of the day before the reporting week starts
    start = date.fromisoformat(r0["start"])
    return {"as_of": (start - timedelta(days=1)).isoformat()}


# AMBER-HOLE:judgment_step4 — judgment for slack.post_message (text)
class Step4Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(description="The five-sentence summary to post.")
    confidence: float = Field(ge=0, le=1)


PROMPT_step4 = (
    "Write the weekly report summary for the #eng channel in exactly five sentences. Use the "
    "orders in `r1`, the top products in `r2` and the RFM snapshot in `r3` for the week given"
    " in `inputs`. Sentence 1: week, number of orders and total revenue. Sentence 2: the "
    "leading product with its revenue and units. Sentence 3: how customers moved between RFM "
    "bands versus the snapshot date. Sentence 4: how many orders came from returning "
    "customers. Sentence 5: point to the shop dashboard for the full lists. Plain prose, no "
    "bullet points, no markdown."
)


@program(workflow="weekly-report", version=1)
def run(inputs: Inputs, ctx: Context) -> Output:
    r0 = ctx.tool("shop.week_range", week=inputs.week)  # step 1
    r1 = ctx.tool("shop.list_orders", since=r0["start"], until=r0["end"])  # step 2
    r2 = ctx.tool("shop.top_products", n=5, since=r0["start"], until=r0["end"])  # step 3
    t3 = transform_step3(inputs, r0, r1, r2)  # transform hole
    r3 = ctx.tool("shop.rfm_snapshot", as_of=t3["as_of"])  # step 4
    s4 = ctx.llm("step4", Step4Out, PROMPT_step4, context={"inputs": inputs.model_dump(), "r0": r0, "r1": r1, "r2": r2, "r3": r3})  # judgment
    r4 = ctx.tool("slack.post_message", channel="#eng", text=s4.text)  # step 5  # effect: write
    return Output(ok=r4["ok"], ts=r4["ts"], text=s4.text)
