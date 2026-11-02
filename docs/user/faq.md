---
title: "FAQ"
tags: [amber/user]
---
# FAQ

**Is this a cache?** No. A cache replays one recorded path. Amber aligns several runs,
generalises the arguments (inputs, references, templates) and emits a program with typed
`llm()` holes, replay tests and guards. Change the input and the program still works; change the
world and the doctor tells you what to do.

**Does it need an API key?** For `compile` (hole synthesis) and for `llm()` steps at run time,
yes: either `ANTHROPIC_API_KEY` (Messages API with structured outputs) or a logged-in `claude`
CLI (`--json-schema`). `align`, `verify`, `doctor` and the panel are offline. Tests use a
`recorded` backend and never touch the network.

**Which backend should I use at run time?** The `anthropic` backend (`ANTHROPIC_API_KEY`): an
`llm()` judgment then costs roughly its prompt plus the schema. The `claude` CLI backend is fine
for `compile` and for trying things out, but every call carries the CLI's own system prompt
(about 30k input tokens per judgment in our runs), which trips the examples'
`max_llm_tokens = 20000` budget guard.

**What about side effects?** Write-effect tools are dry-run by default and return a placeholder
shaped like the recording. Pass `--allow-writes` to execute them. The fallback refuses to
re-run a task from scratch when writes already happened (`writes_already_done`), and refuses
to hand a task with pending writes to the agent when the run was not allowed to write.

**Can I edit `program.py`?** Yes — it is ordinary Python. `amber doctor` reports `program_lint`
as broken when the file no longer matches the manifest hash; `amber verify` tells you whether
your edit still replays. A recompile overwrites it, so keep edits in a new version or re-record.

**What models does it call at run time?** Only the judgment model from `amber.toml`
(`[models].judgment`, a Haiku-class model by default) and only for `llm()` steps, with a
schema-validated answer and a token budget. Everything else is Python.

**Does it send my data anywhere?** Recordings stay in `.amber/` on your machine. Compile sends
the hole's examples (shrunk recorded values) to the model you configured; nothing else leaves.

**Why did a transform become a judgment?** The model could not write a function that reproduces
every recording within the repair budget; Amber downgrades the hole instead of guessing. The
warning is in `REPORT.md`. More runs with clearer inputs usually fix it.

**Windows?** Best effort: the test suite runs on Windows, but the acceptance path in the plan
targets macOS and Linux. `Bash` steps receive the raw command string on Windows.
