# issue-triage example — run from this directory.
WF := issue-triage
AMBER := uv run amber
# explicit model for the recordings (never the user's default); --setting-sources user keeps
# this repository's CLAUDE.md out of the agent's context
AGENT_MODEL ?= claude-sonnet-5-5
# three days whose duplicate sets differ (10-30 has 3 duplicates, the others 4); 10-28 is the
# unseen input used by `make run`
DAYS := 2026-10-27 2026-10-29 2026-10-30

.PHONY: record align compile verify run drift undrift heal demo clean

record:           ## 3 live recordings (needs the claude CLI logged in)
	@for d in $(DAYS); do \
	  $(AMBER) record $(WF) --input since=$$d --mcp mcp.json --allow 'mcp__github__*' --allow 'mcp__slack__*' \
	    --permission-mode acceptEdits -- claude -p "$$(sed "s/{since}/$$d/g" task.md)" --model $(AGENT_MODEL) --setting-sources user; \
	done

align:
	$(AMBER) align $(WF) --show

compile:
	$(AMBER) compile $(WF)

verify:
	$(AMBER) verify $(WF)

run:
	$(AMBER) run $(WF) --input since=2026-10-28 --allow-writes

drift:            ## scenario B: label taxonomy becomes kind/* and add_labels rejects area:* → error guard
	uv run python scenario.py B

undrift:
	uv run python scenario.py A

heal:
	$(AMBER) run $(WF) --input since=2026-10-28 --allow-writes --fallback agent
	$(AMBER) compile $(WF) --refresh
	$(AMBER) verify $(WF)

demo: record align compile verify run

clean:
	rm -rf .amber .outbox programs .scenario
