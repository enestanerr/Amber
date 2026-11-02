"""Tiny stdio MCP server for tests: `echo(text)` (read-only) and `shout(text)` (write)."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("echo")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def echo(text: str) -> dict[str, str]:
    """Return the text unchanged."""
    return {"text": text}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def shout(text: str) -> str:
    """Return the text upper-cased (pretends to have a side effect)."""
    return text.upper()


if __name__ == "__main__":
    mcp.run(transport="stdio")
