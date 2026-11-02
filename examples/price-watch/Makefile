# price-watch example — run from this directory. Needs Node >= 18 for @playwright/mcp.
WF := price-watch
AMBER := uv run amber
# explicit model for the recordings (never the user's default); --setting-sources user keeps
# this repository's CLAUDE.md out of the agent's context
AGENT_MODEL ?= claude-sonnet-5-5
# scenario:product pairs: a quiet day and two price-update days (A2), so the recordings show
# both outcomes; `make run` uses an unseen product on the update day
RUNS := A:all A2:all A2:lantern-brass

.PHONY: site serve record align compile verify run drift undrift heal demo clean

site:             ## (re)generate site/ for the current scenario
	uv run python scenario.py

serve:            ## serve the static catalog on :8765 (keep running in another terminal)
	uv run python -m http.server 8765 --directory site

record:           ## 3 live recordings (needs the claude CLI logged in and `make serve` running)
	@for r in $(RUNS); do \
	  s=$${r%%:*}; p=$${r#*:}; uv run python scenario.py $$s; \
	  $(AMBER) record $(WF) --input product=$$p --tag scenario-$$s --mcp mcp.json --allow 'mcp__playwright__*' --allow 'mcp__alerts__*' \
	    --permission-mode acceptEdits -- claude -p "$$(sed "s/{product}/$$p/g" task.md)" --model $(AGENT_MODEL) --setting-sources user; \
	done
	uv run python scenario.py A

align:
	$(AMBER) align $(WF) --show

compile:
	$(AMBER) compile $(WF)

verify:
	$(AMBER) verify $(WF)

run:              ## unseen input on a price-update day: only the iron torch changed
	uv run python scenario.py A2
	$(AMBER) run $(WF) --input product=torch-iron --allow-writes

drift:            ## scenario B: prices change and the `.price` class becomes `.cost`
	uv run python scenario.py B

undrift:
	uv run python scenario.py A

heal:
	$(AMBER) run $(WF) --input product=all --allow-writes --fallback agent
	$(AMBER) compile $(WF) --refresh
	$(AMBER) verify $(WF)

demo: record align compile verify run

clean:
	rm -rf .amber .outbox programs .scenario .playwright-mcp
