"""Mock Slack MCP server: ``list_channels()`` and ``post_message(channel, text)``.

``post_message`` appends to ``.outbox/slack.jsonl`` (or ``AMBER_OUTBOX``) and returns
``{"ok": true, "ts": "..."}``. It is annotated as not read-only so Amber treats it as a write.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp_common import outbox_path

mcp = MCPServer("slack")

CHANNELS = [
    {"id": "C001", "name": "eng"},
    {"id": "C002", "name": "sales"},
    {"id": "C003", "name": "alerts"},
]


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def list_channels() -> list[dict[str, Any]]:
    """List the channels the bot can post to."""
    return [dict(c) for c in CHANNELS]


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
def post_message(channel: str, text: str) -> dict[str, Any]:
    """Post a message to a channel (recorded in the outbox file)."""
    ts = f"{time.time():.6f}"
    path = outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "channel": channel, "text": text}) + "\n")
    return {"ok": True, "ts": ts}


if __name__ == "__main__":
    mcp.run(transport="stdio")
