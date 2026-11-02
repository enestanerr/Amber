# price-watch example — run from this directory. Needs Node >= 18 for @playwright/mcp.
WF := price-watch
AMBER := uv run amber
PRODUCTS := all lantern-brass all

.PHONY: site serve record align compile verify run drift undrift heal demo clean

site:             ## (re)generate site/ for the current scenario
	uv run python scenario.py

serve:            ## serve the static catalog on :8765 (keep running in another terminal)
	uv run python -m http.server 8765 --directory site

record: site      ## 3 live recordings (needs the claude CLI logged in and `make serve` running)
	@for p in $(PRODUCTS); do \
	  $(AMBER) record $(WF) --input product=$$p --mcp mcp.json --allow 'mcp__playwright__*' --allow 'mcp__alerts__*' \
	    --permission-mode acceptEdits -- claude -p "$$(sed "s/{product}/$$p/g" task.md)"; \
	done

align:
	$(AMBER) align $(WF) --show

compile:
	$(AMBER) compile $(WF)

verify:
	$(AMBER) verify $(WF)

run:
	$(AMBER) run $(WF) --input product=all --allow-writes

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
	rm -rf .amber .outbox programs .scenario
