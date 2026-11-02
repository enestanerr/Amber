"""Runtime judgment context: whole values within the budget, largest value shrunk beyond it."""

from __future__ import annotations

from amber.runtime.judgment import CONTEXT_BYTES, fit_context, render_user
from amber.trace.canonical import canonical_json


def test_context_within_budget_is_kept_whole() -> None:
    orders = [
        {"id": f"o_{i:04d}", "total": i * 1.5, "customer_id": f"c_{i % 40}"} for i in range(75)
    ]
    ctx = {"inputs": {"week": "2026-W44"}, "r2": orders}
    assert fit_context(ctx) == ctx  # a 20-item cut would hide 55 of the 75 orders


def test_over_budget_shrinks_the_largest_value_first() -> None:
    big = [{"id": i, "pad": "x" * 200} for i in range(400)]  # ~90 KB
    small = [{"id": i} for i in range(50)]
    out = fit_context({"big": big, "small": small}, limit=20 * 1024)
    assert out["small"] == small
    assert isinstance(out["big"][-1], str) and out["big"][-1].startswith("...+")
    assert sum(len(canonical_json(v)) for v in out.values()) <= 20 * 1024
    assert CONTEXT_BYTES == 40 * 1024


def test_render_user_is_compact_json_with_step_id() -> None:
    text = render_user("step4", "Write the summary.", {"inputs": {"week": "2026-W44"}})
    head, rendered = text.split("Context (JSON):\n", 1)
    assert head.startswith("Write the summary.")
    assert rendered == '{"context":{"inputs":{"week":"2026-W44"}},"step_id":"step4"}'


def test_config_budgets_apply_at_run_time() -> None:
    """Raising max_llm_tokens in amber.toml must not need a recompile."""
    from amber.compile.manifest import Budgets
    from amber.config import BudgetsCfg
    from amber.runtime.runner import with_config_budgets

    class FakeManifest:
        def __init__(self) -> None:
            self.budgets = Budgets(max_calls=14, max_llm_tokens=20_000, wall_clock_s=600)

        def model_copy(self, update: dict[str, object]) -> FakeManifest:
            out = FakeManifest()
            out.budgets = update["budgets"]  # type: ignore[assignment]
            return out

    got = with_config_budgets(
        FakeManifest(),  # type: ignore[arg-type]
        BudgetsCfg(max_llm_tokens=40_000, wall_clock_s=900, max_calls_factor=9.0),
    )
    assert (got.budgets.max_calls, got.budgets.max_llm_tokens, got.budgets.wall_clock_s) == (
        14,  # still derived from the recordings
        40_000,
        900,
    )
