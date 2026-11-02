# `amber`

Compile recorded agent runs into programs with replay tests, guards and a drift loop.

**Usage**:

```console
$ amber [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-V, --version`: Show the version and exit.
* `--project <path>`: Project root containing amber.toml (default: walk up from the cwd).  [env var: AMBER_PROJECT]
* `--json`: Machine-readable JSON on stdout for the command.
* `--help`: Show this message and exit.

**Commands**:

* `init`: Create .amber/ and amber.toml in the...
* `record`: Record one agent run as a trace.
* `import`: Import a transcript or proxy fragments as...
* `proxy`: Run the recording MCP proxy (used from the...
* `align`: Merge the workflow&#x27;s traces into a...
* `compile`: Codegen + hole synthesis →...
* `verify`: Replay the program against its source...
* `run`: Run the compiled program live against the...
* `doctor`: Check live schemas, recent runs and...
* `ui`: Start the read-mostly web panel over...
* `bench`: Measure agent baseline vs compiled program...
* `scheduler`: Foreground loop: run every due schedule...
* `schedule`: Manage cron-style schedules for compiled...
* `programs`: Inspect compiled program versions.
* `traces`: Inspect recorded traces.

## `amber init`

Create .amber/ and amber.toml in the project directory (idempotent).

**Usage**:

```console
$ amber init [OPTIONS]
```

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--force`: Overwrite an existing amber.toml.
* `--help`: Show this message and exit.

## `amber record`

Record one agent run as a trace.

**Usage**:

```console
$ amber record [OPTIONS] {workflow} [agent]...
```

**Arguments**:

* `workflow`: Workflow name, e.g. weekly-report.  [required]
* `agent...`: Agent command after `--`.

**Options**:

* `--input <str>`: Declared input k=v (repeatable).
* `--mcp <path>`: MCP config JSON passed to the agent.
* `--allow <str>`: Tool allow pattern (repeatable).
* `--permission-mode <str>`
* `--sdk`: Use the Agent SDK adapter (extra ).
* `--from-stdin`: Read stream-json from stdin.
* `--prompt <str>`: Task prompt (with --from-stdin).
* `--tag <str>`: Tag on the trace (repeatable).
* `--status <str>`: Override outcome status.
* `--no-inject-inputs`
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber import`

Import a transcript or proxy fragments as a trace.

**Usage**:

```console
$ amber import [OPTIONS] {workflow} [path]
```

**Arguments**:

* `workflow`: Workflow name.  [required]
* `path`: Claude Code transcript (.jsonl).

**Options**:

* `--latest`: Newest transcript for the cwd.
* `--proxy <path>`: Fold `amber proxy` fragments from DIR.
* `--since <str>`: ISO timestamp; ignore older fragments.
* `--prompt <str>`: Task prompt to store.
* `--input <str>`: Declared input k=v.
* `--tag <str>`
* `--status <str>`: success | failure | unknown.
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber proxy`

Run the recording MCP proxy (used from the host&#x27;s MCP config, not interactively).

**Usage**:

```console
$ amber proxy [OPTIONS] [upstream]...
```

**Arguments**:

* `upstream...`: Upstream server command after `--`.

**Options**:

* `--name <str>`: Server name as the host sees it.  [required]
* `--trace-dir <path>`: Fragment directory.
* `--help`: Show this message and exit.

## `amber align`

Merge the workflow&#x27;s traces into a skeleton (.amber/build/&lt;workflow&gt;/skeleton.json).

**Usage**:

```console
$ amber align [OPTIONS] {workflow}
```

**Arguments**:

* `workflow`: Workflow name.  [required]

**Options**:

* `--show`: Print the step table.
* `--keep-unreferenced`: Do not prune unreferenced reads.
* `--include-failed`: Also align failed traces.
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber compile`

Codegen + hole synthesis → programs/&lt;workflow&gt;/v&lt;N&gt;/ (verify marks it usable).

**Usage**:

```console
$ amber compile [OPTIONS] {workflow}
```

**Arguments**:

* `workflow`: Workflow name.  [required]

**Options**:

* `--refresh`: Re-align with all traces and bump the version.
* `--max-repair <int>`: Repair iterations per hole.  [default: 3]
* `--model <str>`: Synthesis model id (default from amber.toml).
* `--dry-run`: Write the program with unfilled holes.
* `--include-failed`
* `--keep-unreferenced`
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber verify`

Replay the program against its source traces; exit 3 on any failure.

**Usage**:

```console
$ amber verify [OPTIONS] {workflow}
```

**Arguments**:

* `workflow`: Workflow name.  [required]

**Options**:

* `-v, --version <int>`: Program version.
* `--trace <str>`: Replay only these trace ids (repeatable).
* `--live-llm`: Also call the real judgment model and compare.
* `--unordered`: Match recorded calls in any order.
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber run`

Run the compiled program live against the configured MCP servers.

**Usage**:

```console
$ amber run [OPTIONS] {workflow}
```

**Arguments**:

* `workflow`: Workflow name.  [required]

**Options**:

* `--input <str>`: Input k=v (repeatable).
* `-v, --version <int>`: Program version (default: latest usable).
* `--dry-run`: Never execute write-effect tools (the default).
* `--allow-writes`: Execute write-effect tools for real.
* `--fallback <str>`: agent | none (what to do on a guard stop).  [default: none]
* `--timeout <float>`: Wall-clock budget in seconds.
* `--force`: Run a version that did not pass verify.
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber doctor`

Check live schemas, recent runs and traces; print findings and a recommendation.

**Usage**:

```console
$ amber doctor [OPTIONS] {workflow}
```

**Arguments**:

* `workflow`: Workflow name.  [required]

**Options**:

* `--runs <int>`: How many recent runs to inspect.  [default: 10]
* `-v, --version <int>`
* `--no-probe`: Skip connecting to the servers.
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber ui`

Start the read-mostly web panel over .amber/amber.db and programs/.

**Usage**:

```console
$ amber ui [OPTIONS]
```

**Options**:

* `--port <int>`: Port on 127.0.0.1.  [default: 8787]
* `--open`: Open the browser.
* `--help`: Show this message and exit.

## `amber bench`

Measure agent baseline vs compiled program (writes bench/results.{json,md}).

**Usage**:

```console
$ amber bench [OPTIONS] {example}
```

**Arguments**:

* `example`: weekly-report | issue-triage | price-watch | all  [required]

**Options**:

* `--runs <int>`: Runs per side.  [default: 3]
* `--baseline <str>`: record (fresh claude -p runs) or existing (recordings on disk).  [default: record]
* `--offline`: Existing recordings and the recorded model backend.
* `--help`: Show this message and exit.

## `amber scheduler`

Foreground loop: run every due schedule (logs to .amber/logs/scheduler.log).

**Usage**:

```console
$ amber scheduler [OPTIONS]
```

**Options**:

* `--once`: Run due schedules once and exit.
* `--poll <float>`: Poll interval in seconds.  [default: 30.0]
* `--help`: Show this message and exit.

## `amber schedule`

Manage cron-style schedules for compiled programs.

**Usage**:

```console
$ amber schedule [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add a schedule (runs with writes allowed,...
* `ls`: List schedules.
* `rm`: Remove a schedule.

### `amber schedule add`

Add a schedule (runs with writes allowed, fallback none).

**Usage**:

```console
$ amber schedule add [OPTIONS] {workflow}
```

**Arguments**:

* `workflow`: [required]

**Options**:

* `--cron <str>`: Cron expression, e.g. &quot;0 9 * * 1&quot;.  [required]
* `--input <str>`: Input k=v (repeatable).
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

### `amber schedule ls`

List schedules.

**Usage**:

```console
$ amber schedule ls [OPTIONS]
```

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

### `amber schedule rm`

Remove a schedule.

**Usage**:

```console
$ amber schedule rm [OPTIONS] {schedule_id}
```

**Arguments**:

* `schedule_id`: [required]

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber programs`

Inspect compiled program versions.

**Usage**:

```console
$ amber programs [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `ls`: List program versions (usable, traces, llm...
* `show`: Print program.py (or the manifest) of a...
* `diff`: Unified diff of program.py plus a manifest...

### `amber programs ls`

List program versions (usable, traces, llm steps, transforms, created).

**Usage**:

```console
$ amber programs ls [OPTIONS] [workflow]
```

**Arguments**:

* `workflow`: Filter by workflow.

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

### `amber programs show`

Print program.py (or the manifest) of a version.

**Usage**:

```console
$ amber programs show [OPTIONS] {workflow}
```

**Arguments**:

* `workflow`: [required]

**Options**:

* `-v, --version <int>`: Version (default latest).
* `--manifest`: Print the manifest instead of the program.
* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

### `amber programs diff`

Unified diff of program.py plus a manifest delta between two versions.

**Usage**:

```console
$ amber programs diff [OPTIONS] {workflow} {v1} {v2}
```

**Arguments**:

* `workflow`: [required]
* `v1`: [required]
* `v2`: [required]

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

## `amber traces`

Inspect recorded traces.

**Usage**:

```console
$ amber traces [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `ls`: List traces (oldest first).
* `show`: Show a trace as a step table plus its...
* `rm`: Delete a trace (file and index row).

### `amber traces ls`

List traces (oldest first).

**Usage**:

```console
$ amber traces ls [OPTIONS] [workflow]
```

**Arguments**:

* `workflow`: Filter by workflow.

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.

### `amber traces show`

Show a trace as a step table plus its outcome.

**Usage**:

```console
$ amber traces show [OPTIONS] {trace_id}
```

**Arguments**:

* `trace_id`: Trace id (see `amber traces ls`).  [required]

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--full`: Dump the full trace JSON.
* `--help`: Show this message and exit.

### `amber traces rm`

Delete a trace (file and index row).

**Usage**:

```console
$ amber traces rm [OPTIONS] {trace_id}
```

**Arguments**:

* `trace_id`: Trace id to delete.  [required]

**Options**:

* `--json`: Machine-readable JSON on stdout (human output suppressed).
* `--help`: Show this message and exit.
