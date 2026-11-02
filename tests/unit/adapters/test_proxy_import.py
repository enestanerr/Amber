from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from amber.adapters.mcp_proxy import FragmentWriter, import_proxy, read_fragments, tool_effect
from amber.errors import AmberError
from amber.util.json import dumps_compact

PROXY = Path(__file__).resolve().parents[2] / "fixtures" / "mcp_proxy"


def test_tool_effect() -> None:
    assert tool_effect(None) == "unknown"
    assert tool_effect({"read_only_hint": True}) == "read"
    assert tool_effect({"read_only_hint": False}) == "write"
    assert tool_effect({"destructive_hint": True}) == "write"
    assert tool_effect({"idempotent_hint": True}) == "unknown"


def test_import_fixture_fragments_merge_by_timestamp() -> None:
    trace = import_proxy(
        PROXY, workflow="weekly-report", prompt="task", inputs={"week": "2026-W42"}, trace_id="px"
    )
    assert trace.id == "px" and trace.adapter == "mcp-proxy" and trace.origin == "import"
    assert [c.tool for c in trace.calls] == [
        "shop.week_range",
        "shop.list_orders",
        "shop.top_products",
        "shop.rfm_snapshot",
        "slack.post_message",
    ]
    assert [c.call_id for c in trace.calls][:2] == ["px_001", "px_002"]
    orders, post = trace.tool_schema("shop.list_orders"), trace.tool_schema("slack.post_message")
    assert orders is not None and orders.effect == "read"
    assert post is not None and post.effect == "write"
    assert post.input_schema["required"] == ["channel", "text"]
    assert trace.thoughts == [] and trace.outcome.status == "unknown"
    assert trace.created_at == datetime(2026, 10, 19, 9, 0, 5, tzinfo=UTC)


def test_since_filter_and_errors(tmp_path: Path) -> None:
    trace = import_proxy(PROXY, workflow="wf", since=datetime(2026, 10, 19, 9, 0, 30, tzinfo=UTC))
    assert [c.tool for c in trace.calls] == ["slack.post_message"]
    with pytest.raises(AmberError, match="no proxy directory"):
        import_proxy(tmp_path / "missing", workflow="wf")
    (tmp_path / "empty").mkdir()
    with pytest.raises(AmberError, match="no proxy fragments"):
        import_proxy(tmp_path / "empty", workflow="wf")


class _Result:
    def __init__(self, text: str, is_error: bool = False) -> None:
        self.content = [{"type": "text", "text": text}]
        self.structured_content = None
        self.is_error = is_error


def test_fragment_writer_offloads_large_results_and_import_resolves_them(tmp_path: Path) -> None:
    w = FragmentWriter(tmp_path, "big", pid=7)
    big = dumps_compact({"rows": [{"i": i, "pad": "x" * 100} for i in range(1000)]})
    w.write_call("dump", {"n": 1000}, _Result(big), duration_ms=5, schema_hash="abc")
    w.write_call("fail", {}, _Result("boom", is_error=True), duration_ms=1, schema_hash="")
    assert w.path == tmp_path / "big-7.jsonl"
    calls, tools = read_fragments(tmp_path)
    assert tools == {} and "$ref" in calls[0]["result"] and calls[1]["is_error"] is True
    assert (tmp_path / "blobs").is_dir()
    trace = import_proxy(tmp_path, workflow="wf")
    assert len(trace.calls[0].result["rows"]) == 1000 and trace.calls[0].result_kind == "json"
    assert trace.calls[1].is_error and trace.calls[1].result == "boom"
    dump = trace.tool_schema("big.dump")
    assert dump is not None and dump.input_schema == {}  # stub when no tools header
