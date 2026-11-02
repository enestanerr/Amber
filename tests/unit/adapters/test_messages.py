"""Shared Claude mapper: tool names, usage, ANSI, ignored events, denials, goldens."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from amber.adapters.base import RecordRequest
from amber.adapters.claude_messages import (
    ClaudeMessageMapper,
    PermissionPromptError,
    parse_claude_tool_name,
    parse_stream_line,
    strip_ansi,
    usage_from_payload,
)
from amber.adapters.claude_stream import record_from_lines
from amber.adapters.enrich import apply_tool_schemas
from amber.trace.models import ToolCall, Trace
from amber.util.json import read_json
from tests.fixtures.gen_weekly_report import TASK, TOOLS, WEEKS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("mcp__shop__list_orders", ("shop", "list_orders")),
        ("mcp__my-server__tool__with__underscores", ("my-server", "tool__with__underscores")),
        ("Bash", ("builtin", "Bash")),
        ("mcp__broken", ("mcp", "broken")),
    ],
)
def test_parse_claude_tool_name(raw: str, expected: tuple[str, str]) -> None:
    assert parse_claude_tool_name(raw) == expected


def test_usage_mapping() -> None:
    u = usage_from_payload(
        {
            "input_tokens": 1,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 3,
            "output_tokens": 4,
        }
    )
    assert (u.input_tokens, u.cache_read_tokens, u.cache_creation_tokens, u.output_tokens) == (
        1,
        2,
        3,
        4,
    )
    assert usage_from_payload(None).total_input == 0


def test_strip_ansi_and_parse_line() -> None:
    assert strip_ansi('\x1b[2K\x1b[?25l{"a":1}') == '{"a":1}'
    assert parse_stream_line('\x1b[2K{"type":"x"}\n') == {"type": "x"}
    assert parse_stream_line("   ") is None
    assert parse_stream_line("not json") is None
    assert parse_stream_line("[1,2]") is None


def _mapper() -> ClaudeMessageMapper:
    return ClaudeMessageMapper(workflow="wf", adapter="claude-stream", inputs={"k": "v"})


def _tool_use(call_id: str, name: str, args: dict[str, object]) -> dict[str, object]:
    block = {"type": "tool_use", "id": call_id, "name": name, "input": args}
    return {"type": "assistant", "message": {"content": [block]}}


def _tool_result(call_id: str, content: object, is_error: bool = False) -> dict[str, object]:
    block = {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": content,
        "is_error": is_error,
    }
    return {"type": "user", "message": {"content": [block]}}


def test_ignored_events_and_result() -> None:
    m = _mapper()
    m.feed({"type": "stream_event", "event": {}})
    m.feed({"type": "rate_limit_event"})
    m.feed({"type": "system", "subtype": "other"})
    m.feed({"type": "bogus"})
    assert m.events == []
    m.feed(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "num_turns": 2,
        }
    )
    t = m.build(trace_id="t", created_at=T0)
    assert t.outcome.status == "success"
    assert t.outcome.final_text == "done" and t.outcome.num_turns == 2
    assert t.inputs == {"k": "v"}


def test_permission_request_aborts() -> None:
    with pytest.raises(PermissionPromptError) as info:
        _mapper().feed({"type": "permission_request", "tool": "Bash"})
    assert info.value.hint and "--allow" in info.value.hint


def test_denied_tool_results_are_kept_and_counted() -> None:
    m = _mapper()
    m.feed(_tool_use("c1", "Bash", {"command": "ls"}))
    m.feed(_tool_result("c1", "Permission denied: Bash not allowed", is_error=True))
    t = m.build(trace_id="t", created_at=T0)
    call = t.calls[0]
    assert call.is_error and call.result_kind == "text" and m.denied == ["builtin.Bash"]
    assert t.outcome.status == "unknown"  # no result event


def test_unclosed_call_warns_and_unknown_result_ignored() -> None:
    m = _mapper()
    m.feed(_tool_use("c1", "mcp__s__t", {}))
    m.feed(_tool_result("nope", "x"))
    t = m.build(trace_id="t", created_at=T0)
    assert t.calls[0].result_kind == "empty" and m.warnings and "c1" in m.warnings[0]
    assert t.tool_schema("s.t") is not None  # stub added for a tool missing from init


def test_parent_tool_use_id_and_thoughts() -> None:
    m = _mapper()
    text_blocks = [{"type": "text", "text": "  "}, {"type": "text", "text": "plan"}]
    m.feed({"type": "assistant", "message": {"content": text_blocks}})
    block = {"type": "tool_use", "id": "c1", "name": "Read", "input": {"file_path": "a"}}
    m.feed({"type": "assistant", "parent_tool_use_id": "task1", "message": {"content": [block]}})
    t = m.build(trace_id="t", created_at=T0)
    assert [e.kind for e in t.events] == ["thought", "tool_call"]
    call = t.events[1]
    assert isinstance(call, ToolCall)
    assert call.parent_call_id == "task1" and call.tool == "builtin.Read"
    schema = t.tool_schema("builtin.Read")
    assert schema is not None and schema.effect == "read"


def test_user_text_becomes_task_prompt_and_note() -> None:
    m = ClaudeMessageMapper(workflow="wf", adapter="claude-transcript")
    m.feed({"type": "user", "message": {"content": "do the thing"}})
    m.feed({"type": "assistant", "message": {"content": "sure"}})
    t = m.build(trace_id="t", created_at=T0)
    assert t.task_prompt == "do the thing"
    assert [e.kind for e in t.events] == ["note", "thought"]
    assert t.outcome.final_text == "sure"  # last assistant text when no result event


@pytest.mark.parametrize(
    ("run", "trace_id", "week"),
    [(1, "t1", "2026-W42"), (2, "t2", "2026-W43"), (3, "t3", "2026-W44")],
)
def test_stream_fixtures_map_to_golden_traces(run: int, trace_id: str, week: str) -> None:
    path = FIXTURES / "claude_stream" / f"weekly_report_run{run}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    golden = Trace.model_validate(
        read_json(FIXTURES / "traces" / "weekly-report" / f"{trace_id}.json")
    )
    rec = record_from_lines(
        RecordRequest(workflow="weekly-report", agent_command=[], inputs={"week": week}),
        lines,
        task_prompt=TASK.format(week=week),
        trace_id=trace_id,
        created_at=golden.created_at,
    )
    stub = rec.trace.tool_schema("shop.list_orders")
    assert stub is not None and stub.input_schema == {}  # stub before enrichment
    enriched = apply_tool_schemas(rec.trace, TOOLS)
    enriched.harness["version"] = "2.1.316"
    assert enriched.model_dump(mode="json") == golden.model_dump(mode="json")
    assert golden.inputs == {"week": week}
    assert golden.calls[0].result["start"] == WEEKS[week][0]
    assert golden.outcome.status == "success" and golden.outcome.usage.cache_read_tokens == 12000
    assert any("\x1b" in line for line in lines)  # fixture really has an ANSI-prefixed line


def test_harness_internal_tools_are_skipped() -> None:
    """Claude Code's ToolSearch loads deferred tool schemas; it is bookkeeping, not a step."""
    m = _mapper()
    m.feed({"type": "system", "subtype": "init", "tools": ["ToolSearch", "mcp__shop__week_range"]})
    m.feed(_tool_use("c0", "ToolSearch", {"query": "select:mcp__shop__week_range"}))
    m.feed(_tool_result("c0", [{"type": "tool_reference", "tool_name": "mcp__shop__week_range"}]))
    m.feed(_tool_use("c1", "mcp__shop__week_range", {"week": "2026-W42"}))
    m.feed(_tool_result("c1", '{"start": "2026-10-12", "end": "2026-10-18"}'))
    t = m.build(trace_id="t", created_at=T0)
    assert [c.tool for c in t.calls] == ["shop.week_range"]
    assert t.tool_schema("builtin.ToolSearch") is None and not m.warnings
    assert any(getattr(e, "text", "") == "skipped ToolSearch" for e in t.events)
