# issue-triage example — run from this directory.
WF := issue-triage
AMBER := uv run amber
DAYS := 2026-10-27 2026-10-28 2026-10-29

.PHONY: record align compile verify run drift undrift heal demo clean

record:           ## 3 live recordings (needs the claude CLI logged in)
	@for d in $(DAYS); do \
	  $(AMBER) record $(WF) --input since=$$d --mcp mcp.json --allow 'mcp__github__*' --allow 'mcp__slack__*' \
	    --permission-mode acceptEdits -- claude -p "$$(sed "s/{since}/$$d/g" task.md)"; \
	done

align:
	$(AMBER) align $(WF) --show

compile:
	$(AMBER) compile $(WF)

verify:
	$(AMBER) verify $(WF)

run:
	$(AMBER) run $(WF) --input since=2026-10-30 --allow-writes

drift:            ## scenario B: label taxonomy becomes kind/* and add_labels rejects area:* → error guard
	uv run python scenario.py B

undrift:
	uv run python scenario.py A

heal:
	$(AMBER) run $(WF) --input since=2026-10-30 --allow-writes --fallback agent
	$(AMBER) compile $(WF) --refresh
	$(AMBER) verify $(WF)

demo: record align compile verify run

clean:
	rm -rf .amber .outbox programs .scenario
