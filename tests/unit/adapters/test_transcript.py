from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from amber.adapters.claude_transcript import (
    chain,
    encode_cwd,
    import_transcript,
    latest_transcript,
)
from amber.errors import AmberError
from amber.trace.models import Note

SESSION = Path(__file__).resolve().parents[2] / "fixtures" / "claude_transcript" / "session.jsonl"


def test_encode_cwd() -> None:
    assert encode_cwd("/a/b c") == "-a-b c"
    assert encode_cwd("/Users/me/proj") == "-Users-me-proj"
    assert encode_cwd("C:/Users/x/proj") == "C--Users-x-proj"
    assert encode_cwd("C:\\Users\\x\\proj") == "C--Users-x-proj"


def test_import_session_fixture() -> None:
    trace, warnings = import_transcript(
        SESSION, workflow="weekly-report", inputs={"week": "2026-W42"}
    )
    assert warnings == []
    assert trace.adapter == "claude-transcript" and trace.origin == "import"
    assert trace.session_id == "sess-A" and trace.harness["version"] == "2.1.316"
    assert [c.tool for c in trace.calls] == [
        "shop.week_range",
        "shop.list_orders",
        "shop.top_products",
        "slack.post_message",
    ]
    assert all(c.started_at is not None for c in trace.calls)
    assert trace.task_prompt.startswith("Pull the orders for week 2026-W42")
    notes = [e for e in trace.events if isinstance(e, Note)]
    assert any(n.source == "system" and n.text == "compaction" for n in notes)
    assert not any("older prompt" in n.text for n in notes)  # foreign session filtered
    assert "builtin.Bash" not in {c.tool for c in trace.calls}  # sidechain skipped
    assert trace.outcome.status == "unknown"
    assert trace.outcome.final_text == "Posted the weekly summary to #eng."
    assert trace.outcome.usage.output_tokens == 30 + 4 * 50 + 20
    assert trace.created_at.isoformat().startswith("2026-10-19T09:00:00")


def test_status_override() -> None:
    trace, _ = import_transcript(SESSION, workflow="wf", status="success")
    assert trace.outcome.status == "success"


def test_chain_picks_longest_branch_and_warns() -> None:
    lines = [
        {"uuid": "a", "parentUuid": None},
        {"uuid": "b", "parentUuid": "a"},
        {"uuid": "c", "parentUuid": "b"},
        {"uuid": "d", "parentUuid": "a"},  # rewind branch
    ]
    ordered, warnings = chain(lines)
    assert [x["uuid"] for x in ordered] == ["a", "b", "c"]
    assert warnings and "2 branches" in warnings[0]


def test_latest_transcript(tmp_path: Path) -> None:
    cwd = Path("/work/proj")
    folder = tmp_path / ".claude" / "projects" / encode_cwd(cwd)
    folder.mkdir(parents=True)
    old = folder / "aaa.jsonl"
    old.write_text("{}\n")
    agent = folder / "agent-zzz.jsonl"
    agent.write_text("{}\n")
    new = folder / "bbb.jsonl"
    new.write_text("{}\n")
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(agent, (now + 100, now + 100))
    os.utime(new, (now, now))
    assert latest_transcript(cwd, home=tmp_path) == new
    with pytest.raises(AmberError, match="no Claude Code transcripts"):
        latest_transcript(Path("/elsewhere"), home=tmp_path)


def test_missing_file() -> None:
    with pytest.raises(AmberError, match="transcript not found"):
        import_transcript(Path("/nope/x.jsonl"), workflow="wf")
