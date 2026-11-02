# bench/

`run_bench.py` implements the protocol in `docs/09-launch.md`: N `amber record` runs (the agent
baseline) against N `amber run` runs of the compiled program, per example, with medians, wall
time, tool-call fidelity and agent turns. `results.md` / `results.json` are written here and are
the only numbers that may be quoted as Amber's own.

```bash
uv run python bench/run_bench.py --example weekly-report --runs 3          # live: needs the claude CLI
uv run python bench/run_bench.py --all --runs 3
uv run python bench/run_bench.py --example weekly-report --runs 3 --offline   # baseline = existing recordings
```

`--offline` uses whatever recordings are already in the example's `.amber/traces` as the
baseline (after `make seed` those are the synthetic fixtures) and executes the compiled program
against the mock servers with the recorded model backend (`tests/fixtures/model_fills`), so no
model is called: the compiled llm token column is then the recorded backend's prompt-length
estimate, not measured usage. The header of `results.md` states the mode; a live run with
`claude` replaces it.

Note on backends: a judgment call routed through the `claude` CLI backend carries the CLI's own
system prompt (about 30k input tokens per call in our runs), which trips the example's
`max_llm_tokens = 20000` budget guard. For live measurements set `ANTHROPIC_API_KEY` so the
`anthropic` backend is used, or raise the budget in `amber.toml`.
