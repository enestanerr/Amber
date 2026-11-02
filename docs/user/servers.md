---
title: "Writing MCP servers Amber can compile"
tags: [amber/user]
---
# Writing MCP servers Amber can compile

Amber works with any MCP server, but a few habits make the compiled programs sharper.

**Annotate effects.** `ToolAnnotations(read_only_hint=True)` on reads and
`read_only_hint=False` on writes lets the write-effect guard dry-run exactly the right calls.
Without annotations a tool's effect is `unknown`, which Amber treats as a write (conservative).

```python
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("shop")

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def list_orders(since: str, until: str) -> list[dict]:
    """Orders created between two ISO dates (inclusive)."""
    ...

@mcp.tool(annotations=ToolAnnotations(read_only_hint=False))
def post_message(channel: str, text: str) -> dict:
    ...

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Stable shapes.** Result-shape guards are inferred from the recordings (keys present in every
recording become required). Renaming or dropping a key is drift; adding one is a soft event.

**Structured results.** Return JSON (a dict or a list), not prose. Result-shape guards and the
doctor only see structure: a tool that answers with markdown text (as Playwright MCP's
`browser_evaluate` does) can change completely without tripping a guard.

**Batch writes.** Amber 0.1 does not collapse loops, so one call per item (label each issue)
compiles to a fixed number of steps. A batch tool (`add_labels(updates: list[...])`) compiles to
one step whose list argument a single hole fills, and serves any number of items.

**Explicit arguments.** Dates, ids and limits passed as arguments become provenance edges
(`Ref`, `Template`) the compiler can reproduce. Hidden state (the server remembering the last
call) cannot be replayed.

**Idempotent reads.** Verify and the doctor call read tools freely; make them side-effect free.

**Scenarios for drift tests.** The examples read `AMBER_SCENARIO` (or a `.scenario` file) and
change a key, a label taxonomy or a CSS class in scenario `B` — a cheap way to rehearse
`amber doctor` and the fallback before it happens for real.

**Configuration.** Each compiled program's servers come from `amber.toml`:

```toml
[servers.shop]
command = "uv"
args = ["run", "python", "server_shop.py"]
env = { AMBER_SCENARIO = "A" }

[servers.remote]
url = "http://127.0.0.1:9000/mcp"     # streamable HTTP
```
