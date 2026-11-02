from __future__ import annotations

from pathlib import Path

import pytest

from amber.adapters.claude_cli_flags import inputs_suffix, prepare_claude_argv
from amber.errors import UsageError


def test_minimal_command_gets_stream_flags() -> None:
    p = prepare_claude_argv(["claude", "-p", "do the thing"])
    assert p.prompt == "do the thing"
    assert p.argv == ["claude", "-p", "do the thing", "--output-format", "stream-json", "--verbose"]


def test_print_flag_injected_when_missing_and_suffix_applied() -> None:
    p = prepare_claude_argv(["claude", "task"], prompt_suffix=inputs_suffix({"week": "2026-W44"}))
    assert p.argv[:3] == ["claude", "-p", "task\nInputs: week=2026-W44"]
    assert p.prompt.endswith("Inputs: week=2026-W44")


def test_amber_options_translate_to_claude_flags() -> None:
    p = prepare_claude_argv(
        ["claude", "-p", "t", "--model", "sonnet"],
        mcp_config=Path("mcp.json"),
        allow=["mcp__shop__*", "mcp__slack__*"],
        permission_mode="acceptEdits",
    )
    assert p.argv[p.argv.index("--mcp-config") + 1] == "mcp.json"
    assert p.argv[p.argv.index("--allowedTools") + 1] == "mcp__shop__*,mcp__slack__*"
    assert p.argv[p.argv.index("--permission-mode") + 1] == "acceptEdits"
    assert p.argv[p.argv.index("--model") + 1] == "sonnet"


def test_user_flags_are_not_duplicated() -> None:
    cmd = [
        "claude",
        "-p",
        "t",
        "--output-format",
        "stream-json",
        "--verbose",
        "--mcp-config",
        "a.json",
        "b.json",
    ]
    p = prepare_claude_argv(cmd, mcp_config=Path("x.json"))
    assert p.argv.count("--output-format") == 1 and p.argv.count("--verbose") == 1
    assert p.argv.count("--mcp-config") == 1 and "x.json" not in p.argv
    assert p.prompt == "t"  # variadic values were not mistaken for the prompt


def test_conflicting_output_format_rejected() -> None:
    with pytest.raises(UsageError, match="conflicting"):
        prepare_claude_argv(["claude", "-p", "t", "--output-format", "json"])
    with pytest.raises(UsageError, match="conflicting"):
        prepare_claude_argv(["claude", "-p", "t", "--output-format=text"])


def test_non_claude_command_and_missing_prompt_rejected() -> None:
    with pytest.raises(UsageError, match="must start with `claude`"):
        prepare_claude_argv(["codex", "-p", "t"])
    with pytest.raises(UsageError, match="no prompt"):
        prepare_claude_argv(["claude", "-p", "--model", "sonnet"])
    assert prepare_claude_argv(["/usr/local/bin/claude.exe", "-p", "x"]).prompt == "x"


def test_inputs_suffix_empty() -> None:
    assert inputs_suffix({}) == ""


def test_passthrough_args_keeps_user_flags_and_drops_managed_ones() -> None:
    from amber.adapters.claude_cli_flags import passthrough_args

    cmd = [
        "claude", "-p", "do the task", "--model", "claude-sonnet-5-5", "--setting-sources", "user",
        "--output-format", "stream-json", "--verbose", "--mcp-config", "a.json", "b.json",
        "--allowedTools", "mcp__x__*", "--permission-mode", "acceptEdits", "--max-turns=3",
    ]  # fmt: skip
    assert passthrough_args(cmd) == [
        "--model", "claude-sonnet-5-5", "--setting-sources", "user", "--max-turns=3"
    ]  # fmt: skip
    assert passthrough_args(["claude", "-p", "just a prompt"]) == []
    assert passthrough_args([]) == []
