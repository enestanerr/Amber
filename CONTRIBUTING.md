# Contributing to Amber

Thanks for your interest. Amber is early; the fastest way to help is to try the examples,
file precise issues, and send small pull requests.

## Ground rules

- **Synthetic data only.** Fixtures, examples, screenshots and benchmarks must come from the
  seeded generators in this repository. Never commit real transcripts, exports or credentials.
- **Specs win.** `docs/` is the specification; `PLAN.md` is the build plan. If a spec is
  wrong, open an issue or fix the spec in the same PR with a short note.
- **Tests are part of the task.** Unit tests never touch the network. Live tests are marked
  `live` and are run manually.
- **No Claude-specific code** in `src/amber/{trace,align,compile,runtime}`; harness specifics
  live in `adapters/` and `models/`.

## Development setup

```bash
git clone https://github.com/enestanerr/Amber && cd Amber
uv sync --all-extras
uv run pre-commit install
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

## Pull requests

- Conventional Commits (`feat(align): ...`, `fix(runtime): ...`, `docs: ...`).
- Keep modules under ~400 lines; split by responsibility.
- Add or update tests; `ruff`, `pyright` and `pytest -m "not live"` must pass in CI.
- Update `CHANGELOG.md` under *Unreleased* for user-visible changes.

## Reporting security issues

See [SECURITY.md](SECURITY.md).
