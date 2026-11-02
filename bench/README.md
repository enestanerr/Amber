# bench/

`run_bench.py` implements the protocol in `docs/09-launch.md`: the agent baseline (recorded
`claude -p` runs) against the compiled program (`amber run`), per example, with medians,
min/max, API-equivalent cost, wall time, tool-call fidelity and agent turns. `results.md` /
`results.json` are written here and are the only numbers that may be quoted as Amber's own.

```bash
uv run python bench/run_bench.py --all --runs 3                        # fresh recordings, compile, run
uv run python bench/run_bench.py --all --runs 3 --baseline existing    # reuse the recordings on disk
uv run python bench/run_bench.py --example weekly-report --offline     # no model at all
```

- `--baseline record` records N runs per example with the same flags as the example's
  `make record` (explicit `--model claude-sonnet-5-5`, `--setting-sources user`), then compiles.
- `--baseline existing` takes the successful recordings already in each example's
  `.amber/traces` (fallback traces excluded) and runs the compiled program once per recording,
  with the same inputs and the same scenario (`scenario-X` trace tags, e.g. price-watch's A2 day).
- `--offline` is `--baseline existing` with the recorded model backend
  (`tests/fixtures/model_fills`): nothing calls a model, and the compiled llm-token column is
  that backend's prompt-length estimate, not measured usage.

Fidelity has two columns. *Exact* compares the compiled run's tool sequence with the recording
for the same input. *Core* compares it with the recording's calls that the program's steps were
compiled from (`manifest.trace_calls`), so the agent's exploratory reads that alignment pruned
do not count against it.

The committed `results.md` is the live run of 2026-11-02 (claude CLI 2.1.316 on a claude.ai
subscription, agent `claude-sonnet-5-5`, judgments `claude-haiku-5-5`).
