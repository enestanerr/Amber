"""Model backends: recorded fixtures, selection order, CLI/API parsing (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from amber.config import ModelsCfg
from amber.models.anthropic_api import AnthropicBackend
from amber.models.backend import ModelError
from amber.models.claude_cli import ClaudeCliBackend, extract_candidate
from amber.models.recorded import RecordedBackend, default_fills_dir, load_fills
from amber.models.select import resolve_backend


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


async def test_recorded_backend_keys_and_attempts(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(
        json.dumps({"h1": [{"text": "first"}, {"text": "second"}], "s1": {"text": "runtime"}})
    )
    backend = RecordedBackend(directory=tmp_path)
    assert load_fills(tmp_path)["s1"] == [{"text": "runtime"}]
    out, usage = await backend.complete(
        Out, "sys", json.dumps({"hole_id": "h1", "attempt": 0}), model="m", max_tokens=10
    )
    assert out.text == "first" and usage.input_tokens >= 0
    out2, _ = await backend.complete(
        Out, "sys", json.dumps({"hole_id": "h1", "attempt": 5}), model="m", max_tokens=10
    )
    assert out2.text == "second"  # last fill is reused for later attempts
    out3, _ = await backend.complete(
        Out, "sys", json.dumps({"step_id": "s1"}), model="m", max_tokens=10
    )
    assert out3.text == "runtime"
    with pytest.raises(ModelError, match="no recorded model fill"):
        await backend.complete(
            Out, "sys", json.dumps({"hole_id": "missing"}), model="m", max_tokens=10
        )
    assert backend.calls[0] == ("h1", 0)


def test_default_fills_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMBER_MODEL_FILLS", str(tmp_path))
    assert default_fills_dir() == tmp_path
    monkeypatch.delenv("AMBER_MODEL_FILLS")
    assert default_fills_dir().name == "model_fills"


def test_resolve_backend_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMBER_MODEL_BACKEND", "recorded")
    assert resolve_backend(ModelsCfg(backend="anthropic")).name == "recorded"
    monkeypatch.setenv("AMBER_MODEL_BACKEND", "bogus")
    with pytest.raises(ModelError, match="unknown model backend"):
        resolve_backend(None)
    monkeypatch.delenv("AMBER_MODEL_BACKEND")
    assert resolve_backend(ModelsCfg(backend="recorded")).name == "recorded"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key-0000")
    assert resolve_backend(ModelsCfg()).name == "anthropic"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ModelError, match="no model backend"):
        resolve_backend(ModelsCfg())
    assert resolve_backend(ModelsCfg(backend="cli")).name == "cli"


def test_extract_candidate() -> None:
    assert extract_candidate({"structured_output": {"text": "a"}}) == {"text": "a"}
    assert extract_candidate({"result": 'Sure: {"text": "b"} done'}) == {"text": "b"}
    assert extract_candidate({"result": "no json here"}) is None
    assert extract_candidate({}) is None


async def test_cli_backend_parses_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = ClaudeCliBackend()
    payloads = [
        {"result": "garbage"},
        {"structured_output": {"text": "ok"}, "usage": {"input_tokens": 3, "output_tokens": 1}},
    ]
    seen: list[tuple[list[str], str]] = []

    async def fake_run(argv: list[str], stdin: str = "") -> dict[str, Any]:
        seen.append((argv, stdin))
        system_file = Path(argv[argv.index("--system-prompt-file") + 1])
        assert system_file.read_text(encoding="utf-8") == "sys"
        return payloads[len(seen) - 1]

    monkeypatch.setattr(backend, "_run", fake_run)
    out, usage = await backend.complete(Out, "sys", "user", model="claude-haiku-5-5", max_tokens=10)
    assert out.text == "ok" and usage.input_tokens == 3 and len(seen) == 2
    argv, stdin = seen[0]
    assert stdin == "user" and argv[0] == "-p" and "user" not in argv
    assert "--json-schema" in argv and "--output-format" in argv
    # the CLI's own context is stripped and the structured answer needs two turns
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--max-turns") + 1] == "2"
    assert "--exclude-dynamic-system-prompt-sections" in argv and "--no-session-persistence" in argv
    assert "--bare" not in argv  # --bare skips the claude.ai login
    assert not Path(argv[argv.index("--system-prompt-file") + 1]).exists()  # cleaned up

    async def always_bad(argv: list[str], stdin: str = "") -> dict[str, Any]:
        return {"result": "nope"}

    monkeypatch.setattr(backend, "_run", always_bad)
    with pytest.raises(ModelError, match="valid structured output"):
        await backend.complete(Out, "sys", "user", model="m", max_tokens=10)


async def test_anthropic_backend_with_fake_client() -> None:
    class Usage:
        input_tokens = 10
        output_tokens = 4
        cache_read_input_tokens = 2
        cache_creation_input_tokens = 0

    class Response:
        parsed_output = Out(text="parsed")
        usage = Usage()

    class Messages:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def parse(self, **kwargs: Any) -> Response:
            self.kwargs = kwargs
            return Response()

    class Client:
        messages = Messages()

    client = Client()
    out, usage = await AnthropicBackend(client).complete(
        Out, "sys", "user", model="m", max_tokens=5
    )
    assert out.text == "parsed" and usage.total_input == 12 and usage.output_tokens == 4
    assert (
        client.messages.kwargs["output_format"] is Out and client.messages.kwargs["system"] == "sys"
    )

    class Empty(Messages):
        async def parse(self, **kwargs: Any) -> Any:
            class R:
                parsed_output = None
                usage = None

            return R()

    class EmptyClient:
        messages = Empty()

    with pytest.raises(ModelError, match="no structured output"):
        await AnthropicBackend(EmptyClient()).complete(Out, "sys", "user", model="m", max_tokens=5)


def test_cli_effort_low_for_short_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from amber.models.claude_cli import EFFORT_ENV, effort_for

    monkeypatch.delenv(EFFORT_ENV, raising=False)
    assert effort_for(1200) == "low"  # judgments
    assert effort_for(4000) is None  # synthesis keeps the CLI default
    monkeypatch.setenv(EFFORT_ENV, "medium")
    assert effort_for(1200) == "medium"
    monkeypatch.setenv(EFFORT_ENV, "default")
    assert effort_for(1200) is None


async def test_cli_backend_passes_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    from amber.models.claude_cli import EFFORT_ENV

    monkeypatch.delenv(EFFORT_ENV, raising=False)
    backend = ClaudeCliBackend()
    seen: list[list[str]] = []

    async def fake_run(argv: list[str], stdin: str = "") -> dict[str, Any]:
        seen.append(argv)
        return {"structured_output": {"text": "ok"}}

    monkeypatch.setattr(backend, "_run", fake_run)
    await backend.complete(Out, "sys", "user", model="m", max_tokens=1200)
    await backend.complete(Out, "sys", "user", model="m", max_tokens=4000)
    assert seen[0][seen[0].index("--effort") + 1] == "low" and "--effort" not in seen[1]


async def test_cli_backend_reports_cost_and_both_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected first answer still cost tokens; the CLI's own cost figure is kept."""
    backend = ClaudeCliBackend()
    payloads = [
        {
            "result": "garbage",
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "total_cost_usd": 0.01,
        },
        {
            "structured_output": {"text": "ok"},
            "usage": {"input_tokens": 3, "cache_read_input_tokens": 7, "output_tokens": 1},
            "total_cost_usd": 0.02,
        },
    ]
    calls: list[int] = []

    async def fake_run(argv: list[str], stdin: str = "") -> dict[str, Any]:
        calls.append(1)
        return payloads[len(calls) - 1]

    monkeypatch.setattr(backend, "_run", fake_run)
    _, usage = await backend.complete(Out, "sys", "user", model="m", max_tokens=1200)
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (8, 7, 3)
    assert usage.cost_usd is not None and abs(usage.cost_usd - 0.03) < 1e-9


def test_usage_cost_is_optional_and_left_out_when_unknown() -> None:
    from amber.trace.models import Usage

    assert "cost_usd" not in Usage(input_tokens=1).model_dump(mode="json")
    assert (Usage() + Usage()).cost_usd is None
    assert (Usage(cost_usd=0.5) + Usage()).cost_usd == 0.5


def test_optional_backend_is_none_without_claude_or_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`amber run` / `amber scheduler` must work without a model when none is needed (CI has
    neither the claude CLI nor an API key; a logged-in CLI hid this locally)."""
    from amber.models.select import optional_backend

    assert optional_backend(ModelsCfg(backend="auto")) is None
    assert optional_backend(ModelsCfg(backend="recorded")) is not None
    monkeypatch.setenv("AMBER_MODEL_BACKEND", "nope")
    with pytest.raises(ModelError, match="unknown model backend"):
        optional_backend(ModelsCfg())
