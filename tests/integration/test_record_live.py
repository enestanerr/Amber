"""Live recording through the real `claude` CLI against the echo server (marked `live`).

Run manually: `uv run pytest -q -m live tests/integration/test_record_live.py`.
Needs a logged-in claude CLI (or ANTHROPIC_API_KEY) and costs a few hundred tokens.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from amber.adapters.base import RecordRequest
from amber.adapters.claude_stream import run_claude

ECHO = Path(__file__).resolve().parents[1] / "fixtures" / "servers" / "echo_server.py"

pytestmark = pytest.mark.live


def test_record_echo_live(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        json.dumps({"mcpServers": {"echo": {"command": sys.executable, "args": [str(ECHO)]}}})
    )
    request = RecordRequest(
        workflow="echo-live",
        agent_command=[
            "claude",
            "-p",
            "Call the echo tool once with text 'ping', then reply done.",
        ],
        mcp_config=cfg,
        allow=["mcp__echo__*"],
        cwd=tmp_path,
    )
    rec = asyncio.run(run_claude(request))
    assert rec.exit_code == 0, rec.stderr_tail
    assert rec.trace.outcome.status == "success"
    assert any(c.tool == "echo.echo" for c in rec.trace.calls)
    assert rec.trace.outcome.usage.total_input > 0
