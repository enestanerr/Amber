from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amber.adapters.agent_sdk import block_to_dict, load_mcp_servers, sdk_message_to_dict
from amber.adapters.claude_messages import ClaudeMessageMapper
from amber.adapters.enrich import apply_tool_schemas, read_mcp_config, schemas_from_listing
from amber.trace.models import ToolSchema, Trace
from amber.util.json import read_json
from amber.util.time import utcnow

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


# Stand-ins named like the SDK dataclasses (conversion is duck-typed on the class name).
@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any
    is_error: bool = False


@dataclass
class ThinkingBlock:
    thinking: str


@dataclass
class AssistantMessage:
    content: list[Any]
    model: str = "claude-sonnet-5-5"
    parent_tool_use_id: str | None = None
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 10, "output_tokens": 5})


@dataclass
class UserMessage:
    content: Any
    parent_tool_use_id: str | None = None


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, Any]


@dataclass
class ResultMessage:
    subtype: str = "success"
    is_error: bool = False
    result: str = "done"
    session_id: str = "s1"
    duration_ms: int = 100
    num_turns: int = 3
    total_cost_usd: float = 0.01
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 50, "output_tokens": 7})


@dataclass
class StreamEvent:
    event: dict[str, Any]


def test_sdk_messages_feed_the_mapper() -> None:
    msgs = [
        SystemMessage(
            "init",
            {
                "session_id": "s1",
                "model": "claude-sonnet-5-5",
                "tools": ["Read", "mcp__shop__list_orders"],
            },
        ),
        AssistantMessage([ThinkingBlock("hmm"), TextBlock("plan")]),
        AssistantMessage([ToolUseBlock("c1", "mcp__shop__list_orders", {"since": "a"})]),
        UserMessage([ToolResultBlock("c1", [{"type": "text", "text": json.dumps([{"id": 1}])}])]),
        StreamEvent({"type": "x"}),
        UserMessage("free text from the user"),
        ResultMessage(),
    ]
    mapper = ClaudeMessageMapper(workflow="wf", adapter="agent-sdk")
    for m in msgs:
        d = sdk_message_to_dict(m)
        if d is not None:
            mapper.feed(d)
    t = mapper.build(trace_id="t", created_at=utcnow())
    assert t.adapter == "agent-sdk" and t.session_id == "s1"
    assert [e.kind for e in t.events] == ["thought", "tool_call", "note"]
    assert t.calls[0].result == [{"id": 1}] and t.calls[0].result_kind == "json"
    assert t.outcome.status == "success" and t.outcome.usage.output_tokens == 7
    assert block_to_dict(object()) is None and sdk_message_to_dict(object()) is None


def test_load_mcp_servers_and_config(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"shop": {"command": "uv", "args": ["run", "x.py"]}}}))
    assert load_mcp_servers(cfg) == {"shop": {"command": "uv", "args": ["run", "x.py"]}}
    assert read_mcp_config(cfg)["shop"]["command"] == "uv"
    assert load_mcp_servers(None) == {}


def test_apply_tool_schemas_replaces_stubs_only() -> None:
    t = Trace.model_validate(read_json(FIXTURES / "traces" / "weekly-report" / "t1.json"))
    stubbed = t.model_copy(
        update={"tools": [ToolSchema(server=s.server, name=s.name) for s in t.tools]}
    )
    before, after = stubbed.tool_schema("shop.list_orders"), t.tool_schema("shop.list_orders")
    assert before is not None and after is not None and before.hash != after.hash
    listing = [
        {
            "name": "list_orders",
            "input_schema": {"type": "object", "properties": {"since": {"type": "string"}}},
            "annotations": {"read_only_hint": True},
        }
    ]
    enriched = apply_tool_schemas(stubbed, schemas_from_listing("shop", listing))
    lo = enriched.tool_schema("shop.list_orders")
    assert lo is not None and lo.effect == "read" and "since" in lo.input_schema["properties"]
    week_range = enriched.tool_schema("shop.week_range")
    assert week_range is not None and week_range.input_schema == {}  # untouched stub
    enriched.check()
