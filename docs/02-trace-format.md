---
title: "02 — Canonical trace format and adapters"
tags: [amber/spec, trace]
phases: [P1, P2]
status: spec
created: 2026-11-02
---
# 02 — Canonical trace format and adapters

## 1. Canonical trace (schema_version 1)

All adapters produce this shape. Everything downstream (align, compile, verify) depends only
on it.

```python
class Usage(BaseModel):
    input_tokens: int = 0            # non-cached input
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    @property
    def total_input(self) -> int: return self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens

class ToolSchema(BaseModel):
    server: str                      # "shop", "slack", "builtin"
    name: str                        # "list_orders", "Bash"
    canonical: str                   # "shop.list_orders", "builtin.Bash"
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] | None = None
    effect: Literal["read", "write", "unknown"] = "unknown"   # from MCP annotations readOnlyHint/destructiveHint or builtin table
    hash: str                        # sha256(canonical_json(input_schema))[:16]

class ToolCall(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    idx: int                         # position among events, 0-based
    call_id: str                     # harness id (tool_use id) or generated
    parent_call_id: str | None = None
    tool: str                        # canonical name "server.tool"
    args: dict[str, Any]
    result: Any                      # JSON (dict/list/scalar) or str
    result_kind: Literal["json", "text", "empty"]
    is_error: bool = False
    started_at: datetime | None = None
    duration_ms: int | None = None

class Thought(BaseModel):
    kind: Literal["thought"] = "thought"
    idx: int
    text: str                        # assistant text emitted between tool calls (not hidden reasoning)

class Note(BaseModel):
    kind: Literal["note"] = "note"
    idx: int
    source: Literal["user", "system", "adapter"]
    text: str

Event = Annotated[ToolCall | Thought | Note, Field(discriminator="kind")]

class Outcome(BaseModel):
    status: Literal["success", "failure", "unknown"]
    final_text: str = ""
    usage: Usage = Usage()
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None

class Trace(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    workflow: str
    created_at: datetime
    adapter: Literal["claude-stream", "claude-transcript", "agent-sdk", "mcp-proxy"]
    harness: dict[str, str] = {}     # {"name": "claude-code", "version": "...", "model": "..."}
    task_prompt: str = ""
    inputs: dict[str, Any] = {}      # declared with --input k=v at record time
    tools: list[ToolSchema] = []
    events: list[Event]
    outcome: Outcome
    origin: Literal["record", "import", "fallback"] = "record"
    resumed_from_step: int | None = None
    prefix_calls: list[ToolCall] = []   # for fallback/resumed traces: the compiled run's completed calls before resumed_from_step
    session_id: str | None = None
    tags: list[str] = []
```

Invariants (enforced by `Trace.model_validate` + a `check()` method, tested):
- `events[i].idx == i`.
- Every `ToolCall.tool` has a matching `ToolSchema.canonical` in `tools` (adapters add a stub
  schema with `input_schema={}` when the harness did not expose one).
- `call_id` unique within a trace.

## 2. Canonicalization (`amber.trace.canonical`)

- `canonical_tool_name(server, tool) = f"{server}.{tool}"`. Claude's MCP naming
  `mcp__<server>__<tool>` → `(<server>, <tool>)`. Builtins (`Bash, Read, Write, Edit, Glob,
  Grep, WebFetch, WebSearch, Task, NotebookEdit, TodoWrite, ...`) → `("builtin", name)`.
- `canonical_json(obj)`: `json.dumps(obj, sort_keys=True, ensure_ascii=False,
  separators=(",", ":"))`; floats formatted with `repr`; `-0.0 → 0.0`; NaN rejected.
- `canonicalize_args(args, volatile_paths)`: deep copy; for each volatile JSON path (e.g.
  `$.since`, `$.items[*].id`) replace the value with the sentinel `"<volatile>"`; strip
  trailing whitespace in strings; keep everything else byte-exact.
- `args_fingerprint(args) = sha256(canonical_json(canonicalize_args(args, [])))[:16]`.
- `normalize_result(mcp_result)`:
  1. if `structured_content` present → that JSON (`result_kind="json"`);
  2. else join `TextContent.text` blocks with `"\n"`; if the joined text parses as JSON →
     parsed (`"json"`), else the text (`"text"`); image/audio/resource blocks → replaced by
     `{"$blob": {"type": ..., "sha256": ..., "bytes": n}}` and stored under `.amber/blobs/`;
  3. empty → `None`, `result_kind="empty"`.
  Builtin tool results (strings) go through step 2.
- JSON paths: a tiny subset — `$.a.b`, `$.a[0]`, `$.a[*]` — implemented in
  `amber.trace.jsonpath` (no external dependency). `iter_leaves(obj)` yields `(path, value)`
  for scalars; lists use `[i]`; `[*]` only appears in `volatile_paths` patterns.

## 3. Adapters

### A1. `claude-stream` — wrapping `claude -p`

Invocation produced by `amber record`:

```
claude -p "<task>" --output-format stream-json --verbose [--mcp-config cfg.json] [--allowedTools "mcp__shop__*,mcp__slack__*"] [--permission-mode acceptEdits] [user flags...]
```

Facts to rely on (verified against the CLI's documented behaviour; re-check with `claude --help`):
- `--output-format stream-json` **requires** `--verbose`.
- stdout is NDJSON, one JSON object per line, discriminated by `type`. ANSI escape sequences
  can appear in stdout; strip `\x1b\[[0-9;?]*[A-Za-z]` before parsing; skip blank/non-JSON
  lines with a debug log.
- Event types and the fields we read:

| `type` | fields | mapping |
|---|---|---|
| `system` (`subtype: "init"`) | `session_id`, `model`, `tools[]`, `mcp_servers[]`, `cwd` | `Trace.session_id`, `harness.model`, stub `ToolSchema`s for listed tools |
| `assistant` | `message.content[]` with `{"type":"text","text"}` and `{"type":"tool_use","id","name","input"}`, `message.usage`, optional `parent_tool_use_id` | text → `Thought`; tool_use → open `ToolCall(call_id=id, tool=canon(name), args=input)`; usage accumulated into `Outcome.usage` (sum over assistant messages; if `result.usage` exists prefer it) |
| `user` | `message.content[]` with `{"type":"tool_result","tool_use_id","content","is_error"}` | closes the open call; `content` is a string or a list of `{"type":"text","text"}` blocks → `normalize_result` |
| `result` | `subtype` (`success` / `error_*`), `is_error`, `result`, `session_id`, `duration_ms`, `num_turns`, `total_cost_usd`, `usage` | `Outcome` |
| `stream_event`, `rate_limit_event`, `permission_request`, `system` other subtypes | — | ignored (debug log). If a `permission_request` ever appears (only with a permission prompt tool configured) abort with a hint to pass `--allow` / `--permission-mode` |

Permission denials: in `-p` mode a tool that is not pre-approved is denied and shows up as a
`tool_result` with `is_error: true` and a permission message. The recorder detects the
pattern (`permission`/`not allowed`/`denied` in an error result) and warns at the end:
"N tool calls were denied; re-record with `--allow 'mcp__<server>__*'`". Denied calls are kept
in the trace (`is_error=True`) and align ignores error calls that have no downstream refs.

Abbreviated shape examples (for fixtures; keep fixtures shape-correct, values synthetic):

```json
{"type":"system","subtype":"init","session_id":"s_1","model":"claude-sonnet-5-5","tools":["Bash","Read","mcp__shop__list_orders"],"mcp_servers":[{"name":"shop","status":"connected"}],"cwd":"/work"}
{"type":"assistant","session_id":"s_1","message":{"id":"msg_1","role":"assistant","content":[{"type":"text","text":"I'll pull last week's orders first."}],"usage":{"input_tokens":1200,"output_tokens":40}}}
{"type":"assistant","session_id":"s_1","message":{"id":"msg_2","role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"mcp__shop__list_orders","input":{"since":"2026-10-26","until":"2026-11-01"}}],"usage":{"input_tokens":1300,"output_tokens":60}}}
{"type":"user","session_id":"s_1","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1","content":[{"type":"text","text":"[{\"id\":\"o_1\",\"total\":42.5}]"}]}]}}
{"type":"result","subtype":"success","is_error":false,"session_id":"s_1","duration_ms":18000,"num_turns":4,"total_cost_usd":0.031,"result":"Posted the summary.","usage":{"input_tokens":5100,"cache_read_input_tokens":12000,"cache_creation_input_tokens":800,"output_tokens":900}}
```

Usage field names in Claude payloads: `input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens` → map to `Usage` fields.

Prompt handling: the text after `-p` (or the positional after `--`) is stored as
`task_prompt`. `--input k=v` values are stored in `inputs` and also appended to the prompt as
a line `Inputs: k=v, ...` unless `--no-inject-inputs` is given (so the agent sees the same
parameters the compiled program will receive).

Exit handling: if `claude` exits non-zero or no `result` event arrives, save the trace with
`outcome.status="failure"` and exit 1; the trace is still useful for debugging but align
excludes failures by default (`--include-failed` to override).

### A2. `claude-transcript` — importing `~/.claude/projects/**/*.jsonl`

Location: `~/.claude/projects/<encoded cwd>/<session-id>.jsonl`, where the encoding replaces
`/` with `-` (`/Users/me/proj` → `-Users-me-proj`). Subagent sessions are
`agent-<id>.jsonl` in the same folder and are ignored in v0.1.

Line format: one JSON object per line. Fields used:

| field | use |
|---|---|
| `type` ∈ `user` / `assistant` / `system` / others | drive mapping; others ignored |
| `uuid`, `parentUuid` | reconstruct order: follow the chain from the root (`parentUuid == null`) to the leaf; when multiple leaves exist (branches after `/rewind`), take the longest chain and warn |
| `sessionId` | **filter**: keep only lines whose `sessionId` equals the file's own session id (continuation files carry copied history from earlier sessions) |
| `isSidechain` | skip `true` |
| `isMeta` | skip `true` |
| `message.role`, `message.content` | as in A1: string content = user text (`Note(source="user")`; the first one is `task_prompt`), list content = blocks (`text`, `thinking` (ignored), `tool_use`, `tool_result`) |
| `message.usage` (assistant) | sum into `Outcome.usage` |
| `message.model` | `harness.model` |
| `timestamp`, `cwd`, `version`, `gitBranch` | `started_at` for calls; `harness.version` |
| `type: "system"`, `subtype: "compact_boundary"` | mark a `Note(source="system", text="compaction")`; continue — calls before and after remain one trace |
| `toolUseResult` | ignored (summary field) |

Outcome for transcripts: `status="unknown"` unless `--status success|failure` is given;
`final_text` = last assistant text.

### A3. `agent-sdk` — in-process via `claude_agent_sdk`

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, UserMessage, SystemMessage, ResultMessage, TextBlock, ToolUseBlock, ToolResultBlock
options = ClaudeAgentOptions(cwd=..., mcp_servers={...}, allowed_tools=[...], permission_mode="acceptEdits", model=...)
async for msg in query(prompt=task, options=options): ...
```

Map dataclasses to the dict shapes of A1 (`AssistantMessage.content` → blocks;
`ToolResultBlock.tool_use_id/content/is_error`; `ResultMessage` → `result` event with
`usage`, `total_cost_usd`, `duration_ms`, `num_turns`, `session_id`) and reuse the same
mapper. Requires the `claude` CLI installed (the SDK spawns it). Keep behind the `[sdk]` extra
and import lazily.

### A4. `mcp-proxy` — harness-agnostic capture

A stdio MCP server that fronts an upstream server and logs every call. Built on the `mcp`
v2 low-level API:

```python
from mcp import Client, StdioServerParameters
from mcp.server import Server, ServerRequestContext
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, PaginatedRequestParams, Tool, TextContent

async def on_list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
    res = await upstream.list_tools()            # forward verbatim; record schema hashes once
    return ListToolsResult(tools=res.tools)

async def on_call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    t0 = time.monotonic()
    res = await upstream.call_tool(params.name, params.arguments or {})
    append_fragment(tool=params.name, args=params.arguments, result=res, duration_ms=...)
    return res                                    # forward verbatim (content, structured_content, is_error)

server = Server("amber-proxy", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
# upstream = Client(StdioServerParameters(command=..., args=..., env=os.environ | extra_env))
# run the server over stdio streams: use the SDK's stdio server helper (mcp.server.stdio) and
# server.run(read_stream, write_stream, server.create_initialization_options())
```

Notes:
- Low-level handlers that raise become protocol errors; wrap upstream failures and return
  `CallToolResult(is_error=True, content=[TextContent(type="text", text=str(e))])` so the
  host sees a tool error, and log it as `is_error=True`.
- mcp v2 stdio children receive a minimal environment allow-list; pass `env=` explicitly.
- Fragment line: `{"ts": iso, "server": name, "tool": ..., "args": {...}, "result": <normalized>,
  "is_error": bool, "duration_ms": int, "schema_hash": "..."}`. Oversized results (> 64 KB)
  go to `DIR/blobs/<sha>.json` with `"result": {"$ref": "blobs/<sha>.json"}`.
- Host configuration example (Claude Code `--mcp-config`):
  ```json
  {"mcpServers": {"shop": {"command": "amber", "args": ["proxy", "--name", "shop", "--trace-dir", ".amber/proxy", "--", "uv", "run", "examples/weekly-report/server_shop.py"]}}}
  ```
- `amber import <workflow> --proxy .amber/proxy [--since ISO]` builds a trace from all
  fragments across servers, ordered by `ts`, with `Thought`s absent, `task_prompt` from
  `--prompt` if given.

## 4. Fixtures required by tests

- `tests/fixtures/claude_stream/weekly_report_run{1,2,3}.jsonl`: 3 runs of the weekly-report
  workflow against the mock shop; differ only in `since/until` inputs, order ids, totals and
  the summary text. Include one `stream_event` and one `rate_limit_event` line to prove they
  are ignored, and one ANSI-prefixed line.
- `tests/fixtures/claude_transcript/session.jsonl`: 1 session with a `compact_boundary`,
  a sidechain line, a line from a foreign `sessionId`, and 4 tool calls.
- `tests/fixtures/mcp_proxy/shop-1234.jsonl`, `slack-1235.jsonl`.
- `tests/fixtures/traces/weekly-report/t1..t3.json`: canonical traces equivalent to the
  stream fixtures (golden outputs of the mapper — generate once, review, commit).
- `tests/fixtures/traces/weekly-report/t_broken.json`: same workflow but with steps 2 and 3
  swapped and one extra call, used to exercise `ReplayDivergence` (verify must exit 3).
- `tests/fixtures/model_fills/*.json`: recorded hole fills for the `recorded` model backend
  (one transform, one judgment, one gate) matching the fixture skeleton.
