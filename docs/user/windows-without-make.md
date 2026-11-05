# Running the examples on Windows without `make`

> **Verification status — read this first.** The runner below was verified
> end to end on Linux (not on Windows): in a scratch copy of this checkout
> it ran the weekly-report offline demo to a usable program, `verify 3/3`,
> and a successful dry-run, and it ran `drift`/`undrift`/`clean` for
> weekly-report and price-watch. It has **not** been run on Windows, in
> PowerShell or `cmd`, and the live targets (which need a logged-in
> `claude` CLI, and for price-watch a browser MCP server) have not been
> run through it at all. A Windows reviewer should run at least
> `demo-offline` once on Windows before treating this page as verified.
> Do not claim Windows was tested unless it was.

The example `Makefile`s are thin wrappers around `uv run amber` and
`python scenario.py`, plus Unix-only glue: `sed` for task-text
substitution, shell `for` loops for recordings, `cp` for seeding, and
`$(shell cat task.md)`. `scripts/run_example.py` (standard library only)
performs exactly the same steps as a Python program, with no shell, so it
runs from PowerShell or `cmd` without `make` or Git Bash.

## Prerequisites

- Python 3.12+ and `uv` (for example `winget install astral-sh.uv`, then
  `uv sync --all-extras` once from the repository root). `uv` is still
  required: `uv run amber` manages the project environment, and the
  examples' `amber.toml` / `mcp.json` spawn their mock servers with
  `uv run python ...`. This script removes only the `make` dependency.
- The live targets (`record`, `demo`) additionally need a logged-in
  `claude` CLI, exactly as the Makefiles did. Without it the runner
  refuses `record` with a clear error instead of failing halfway.

## Commands

From the repository root:

```powershell
python scripts/run_example.py --list
python scripts/run_example.py weekly-report demo-offline
python scripts/run_example.py weekly-report drift
python scripts/run_example.py weekly-report undrift
python scripts/run_example.py weekly-report heal
python scripts/run_example.py price-watch drift
python scripts/run_example.py price-watch serve
```

Targets per example (the same set as the Makefiles, plus `site`/`serve`
only for price-watch and `seed`/`demo-offline` only for weekly-report):

| Example | Targets |
|---|---|
| weekly-report | `record`, `align`, `compile`, `verify`, `run`, `drift`, `undrift`, `heal`, `demo`, `clean`, `seed`, `demo-offline` |
| issue-triage | `record`, `align`, `compile`, `verify`, `run`, `drift`, `undrift`, `heal`, `demo`, `clean` |
| price-watch | `record`, `align`, `compile`, `verify`, `run`, `drift`, `undrift`, `heal`, `demo`, `clean`, `site`, `serve` |

The root `Makefile` shortcuts also work as single arguments: `demo` and
`demo-offline` mean the weekly-report targets, `drift` and `heal` mean
the price-watch targets. For example, `python scripts/run_example.py demo-offline`
is the Windows equivalent of `make demo-offline`.

## What the translations do

- `demo-offline` (weekly-report only): seeds the three synthetic fixture
  recordings, compiles with the recorded model fills
  (`AMBER_MODEL_BACKEND=recorded`), verifies, and dry-runs week 2026-W44.
  No `claude` login is needed.
- `record` / `demo`: loop over the Makefile's weeks, days, or
  scenario/product pairs, substituting `{week}`, `{since}`, or `{product}`
  in `task.md` with plain string replacement (no `sed`), and restore
  scenario A after the price-watch recording loop, as the Makefile did.
- `drift` / `undrift`: run `scenario.py B` / `scenario.py A`.
- `heal`: run with `--fallback agent`, then `compile --refresh`, then
  `verify`, as the Makefiles did.
- `serve` (price-watch only): serves the static catalog on port 8765 in
  the foreground; stop it with Ctrl+C, as with `make serve`.
- `clean`: removes runtime output only (`.amber`, `.outbox`, `programs`,
  `.scenario`, plus `.playwright-mcp` for price-watch). It never touches
  the example sources.
- `--dry-run` prints every command with its working directory and extra
  environment variables without running anything, which is how the
  translations were checked for the targets that need live tools:

```powershell
python scripts/run_example.py --dry-run price-watch demo
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `neither 'uv' nor 'amber' is on PATH` | Install `uv` and run `uv sync --all-extras` from the repository root; the runner then uses `uv run amber`. |
| `record needs a logged-in 'claude' CLI` | Log in to the `claude` CLI, or use the offline target (`weekly-report demo-offline`) instead. |
| A server fails to start during `run` | The mock servers are spawned from `amber.toml` / `mcp.json` with `uv run python ...`; make sure `uv` is on `PATH` in the same terminal. |
| `demo-offline` was run in the wrong folder | Run from the repository root; the runner resolves examples relative to its own location, and `amber` resolves the project from the example directory. |
