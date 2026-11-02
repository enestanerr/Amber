"""Offline round-trip: host (mcp.Client) → `amber proxy` (stdio) → echo server.

Runnable as a script (prints `OK <n> fragments`) and as a pytest test.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from amber.adapters.mcp_proxy import import_proxy, read_fragments

ROOT = Path(__file__).resolve().parents[1]
ECHO = ROOT / "fixtures" / "servers" / "echo_server.py"


async def roundtrip(trace_dir: Path) -> int:
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "amber",
            "proxy",
            "--name",
            "echo",
            "--trace-dir",
            str(trace_dir),
            "--",
            sys.executable,
            str(ECHO),
        ],
        env={**os.environ, "AMBER_LOG": "warning"},
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools.tools)
        assert names == ["echo", "shout"], names
        r1 = await client.call_tool("echo", {"text": "hello"})
        assert not r1.is_error
        r2 = await client.call_tool("shout", {"text": "hello"})
        assert not r2.is_error
        r3 = await client.call_tool("echo", {})  # missing arg → tool error, not a crash
        assert r3.is_error
    calls, schemas = read_fragments(trace_dir)
    assert [c["tool"] for c in calls] == ["echo", "shout", "echo"]
    assert schemas["echo.echo"].effect == "read" and schemas["echo.shout"].effect == "write"
    assert calls[0]["result"] == {"text": "hello"} and calls[2]["is_error"] is True
    trace = import_proxy(trace_dir, workflow="echo-test", prompt="roundtrip")
    assert trace.adapter == "mcp-proxy" and len(trace.calls) == 3
    return len(calls)


def test_proxy_roundtrip(tmp_path: Path) -> None:
    assert asyncio.run(roundtrip(tmp_path / "proxy")) == 3


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        n = asyncio.run(roundtrip(Path(d) / "proxy"))
    print(f"OK {n} fragments")
