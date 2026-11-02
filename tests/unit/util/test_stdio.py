"""``errlog_stream`` must always return a stream with a file descriptor (subprocess stderr)."""

from __future__ import annotations

import io
import sys

import pytest

from amber.util.stdio import errlog_stream, stdio_transport


def test_errlog_falls_back_when_stderr_has_no_fileno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", io.StringIO())  # what CliRunner / capsys install
    stream = errlog_stream()
    assert stream is not sys.stderr
    stream.fileno()  # does not raise


def test_errlog_uses_devnull_as_last_resort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setattr(sys, "__stderr__", None)
    stream = errlog_stream()
    stream.fileno()
    assert errlog_stream() is stream  # opened once


def test_stdio_transport_is_an_async_context_manager() -> None:
    from mcp import StdioServerParameters

    transport = stdio_transport(StdioServerParameters(command=sys.executable, args=["-c", ""]))
    assert hasattr(transport, "__aenter__") and hasattr(transport, "__aexit__")


def test_display_path_is_relative_inside_the_cwd(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Messages must not print the user's home directory (it ends up in demo recordings)."""
    from amber.util.paths import display_path

    monkeypatch.chdir(tmp_path)
    inside = tmp_path / "programs" / "wf" / "v1"
    assert display_path(inside) == "programs/wf/v1"
    outside = tmp_path.parent / "elsewhere"
    assert display_path(outside) == str(outside)
