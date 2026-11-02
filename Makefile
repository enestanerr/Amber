# Amber — developer targets. Every target is a thin wrapper around `uv run`.
.PHONY: setup test test-live lint fmt typecheck check demo drift heal bench ui clean

setup:            ## install everything (all extras) and the pre-commit hook
	uv sync --all-extras
	uv run pre-commit install

test:             ## unit tests (offline)
	uv run pytest -q

test-live:        ## live tests (need ANTHROPIC_API_KEY or a logged-in claude CLI)
	uv run pytest -q -m live

lint:             ## ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt:              ## ruff autofix + format
	uv run ruff check . --fix
	uv run ruff format .

typecheck:        ## pyright
	uv run pyright

check: lint typecheck test   ## everything CI runs

demo:             ## end-to-end on examples/weekly-report (record → align → compile → verify → run)
	$(MAKE) -C examples/weekly-report demo

drift:            ## price-watch drift story: switch the site to scenario B
	$(MAKE) -C examples/price-watch drift

heal:             ## price-watch: run --fallback agent → compile --refresh → verify
	$(MAKE) -C examples/price-watch heal

bench:            ## measured numbers for all examples → bench/results.md
	uv run python bench/run_bench.py --all --runs 3

ui:               ## local panel on http://127.0.0.1:8787
	uv run amber ui --port 8787

clean:            ## remove caches and build output (never touches examples' programs/)
	rm -rf dist build .pytest_cache .ruff_cache .coverage htmlcov coverage.xml
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
