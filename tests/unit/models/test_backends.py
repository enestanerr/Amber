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
    seen: list[list[str]] = []

    async def fake_run(argv: list[str]) -> dict[str, Any]:
        seen.append(argv)
        return payloads[len(seen) - 1]

    monkeypatch.setattr(backend, "_run", fake_run)
    out, usage = await backend.complete(Out, "sys", "user", model="claude-haiku-5-5", max_tokens=10)
    assert out.text == "ok" and usage.input_tokens == 3 and len(seen) == 2
    assert "--json-schema" in seen[0] and "--output-format" in seen[0] and seen[0][1] == "user"

    async def always_bad(argv: list[str]) -> dict[str, Any]:
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
