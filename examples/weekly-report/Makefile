# weekly-report example — every target runs from this directory (its own Amber project).
WF := weekly-report
AMBER := uv run amber
# explicit model for the recordings (never the user's default); --setting-sources user keeps
# this repository's CLAUDE.md out of the agent's context
AGENT_MODEL ?= claude-sonnet-5-5
WEEKS := 2026-W42 2026-W43 2026-W44
TASK := $(shell cat task.md)

.PHONY: record align compile verify run drift undrift heal demo seed demo-offline clean

record:           ## 3 live recordings with different inputs (needs the claude CLI logged in)
	@for w in $(WEEKS); do \
	  $(AMBER) record $(WF) --input week=$$w --mcp mcp.json --allow 'mcp__shop__*' --allow 'mcp__slack__*' \
	    --permission-mode acceptEdits -- claude -p "$$(sed "s/{week}/$$w/g" task.md)" --model $(AGENT_MODEL) --setting-sources user; \
	done

align:            ## show the skeleton
	$(AMBER) align $(WF) --show

compile:          ## codegen + hole synthesis + verify
	$(AMBER) compile $(WF)

verify:           ## replay against every source trace
	$(AMBER) verify $(WF)

run:              ## live run against the mock servers (writes allowed: posts to .outbox/slack.jsonl)
	$(AMBER) run $(WF) --input week=2026-W40 --allow-writes

drift:            ## switch the shop to scenario B (list_orders returns `amount` instead of `total`)
	uv run python scenario.py B

undrift:          ## back to scenario A
	uv run python scenario.py A

heal:             ## fallback to the agent, recompile, verify
	$(AMBER) run $(WF) --input week=2026-W41 --allow-writes --fallback agent
	$(AMBER) compile $(WF) --refresh
	$(AMBER) verify $(WF)

demo: record align compile verify run

seed:             ## OFFLINE: use the synthetic fixture recordings instead of live `claude` runs
	mkdir -p .amber/traces/$(WF)
	cp ../../tests/fixtures/traces/$(WF)/t1.json ../../tests/fixtures/traces/$(WF)/t2.json ../../tests/fixtures/traces/$(WF)/t3.json .amber/traces/$(WF)/

demo-offline: seed  ## OFFLINE: compile with recorded model fills, verify, dry-run against the mock servers
	AMBER_MODEL_BACKEND=recorded AMBER_MODEL_FILLS=../../tests/fixtures/model_fills $(AMBER) compile $(WF)
	$(AMBER) verify $(WF)
	AMBER_MODEL_BACKEND=recorded AMBER_MODEL_FILLS=../../tests/fixtures/model_fills $(AMBER) run $(WF) --input week=2026-W44 --dry-run

clean:
	rm -rf .amber .outbox programs .scenario
