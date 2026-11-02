"""Builtin backend policy: path confinement, Bash template + deny-list, WebFetch cap."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
import pytest

from amber.config import BuiltinsCfg
from amber.runtime.backends.builtins import BuiltinBackend, BuiltinPolicyError


@pytest.fixture
def work(tmp_path: Path) -> Path:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("alpha\nbeta gamma\n", encoding="utf-8")
    (tmp_path / "notes" / "b.txt").write_text("beta only\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    return tmp_path


async def test_read_glob_grep_write(work: Path) -> None:
    b = BuiltinBackend(work / "notes")
    assert await b.call("Read", {"file_path": "a.md"}) == "alpha\nbeta gamma\n"
    assert await b.call("Read", {"file_path": "a.md", "offset": 1, "limit": 1}) == "beta gamma\n"
    assert await b.call("Glob", {"pattern": "*.md"}) == ["a.md"]
    hits = await b.call("Grep", {"pattern": "beta"})
    assert [(h["file"], h["line"]) for h in hits] == [("a.md", 2), ("b.txt", 1)]
    written = await b.call("Write", {"file_path": "out/c.txt", "content": "hi"})
    assert written["ok"] and (work / "notes" / "out" / "c.txt").read_text() == "hi"


async def test_paths_are_confined(work: Path) -> None:
    b = BuiltinBackend(work / "notes")
    with pytest.raises(BuiltinPolicyError, match="outside"):
        await b.call("Read", {"file_path": "../outside.txt"})
    with pytest.raises(BuiltinPolicyError):
        await b.call("Write", {"file_path": str(work / "outside.txt"), "content": "x"})
    roots = BuiltinBackend(work / "notes", BuiltinsCfg(read_roots=[str(work)]))
    assert await roots.call("Read", {"file_path": "../outside.txt"}) == "secret"
    with pytest.raises(BuiltinPolicyError):  # read roots never allow writes
        await roots.call("Write", {"file_path": "../outside.txt", "content": "x"})


async def test_unsupported_builtin(work: Path) -> None:
    with pytest.raises(BuiltinPolicyError, match="cannot be run"):
        await BuiltinBackend(work).call("Edit", {})


async def test_bash_template_and_denylist(work: Path) -> None:
    py = re.escape(sys.executable)
    b = BuiltinBackend(work, bash_templates={3: py + r" -c (.*?)"})
    out = await b.call("Bash", {"command": f'{sys.executable} -c "print(21*2)"'}, step_idx=3)
    assert out.strip() == "42"
    with pytest.raises(BuiltinPolicyError, match="recorded template"):
        await b.call("Bash", {"command": "echo other"}, step_idx=3)
    with pytest.raises(BuiltinPolicyError, match="recorded template"):
        await b.call("Bash", {"command": "echo x"}, step_idx=9)
    deny = BuiltinBackend(work, bash_templates={0: "(.*?)"})
    with pytest.raises(BuiltinPolicyError, match="deny-list"):
        await deny.call("Bash", {"command": "sudo rm -rf /"}, step_idx=0)
    with pytest.raises(BuiltinPolicyError, match="pipes"):
        await deny.call("Bash", {"command": "ls | wc -l"}, step_idx=0)
    failing = await b.call(
        "Bash", {"command": f'{sys.executable} -c "import sys; sys.exit(3)"'}, step_idx=3
    )
    assert "[exit code 3]" in failing


async def test_webfetch_strips_html_and_caps_size(work: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/page":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body><h1>Hi</h1><script>x()</script>"
                "<p>there &amp; back</p></body></html>",
            )
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * (10 * 1024 * 1024 + 1))
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    b = BuiltinBackend(work, http_client=client)
    assert (
        await b.call("WebFetch", {"url": "http://example.test/page", "prompt": "ignored"})
        == "Hi there & back"
    )
    with pytest.raises(BuiltinPolicyError, match="exceeds"):
        await b.call("WebFetch", {"url": "http://example.test/big"})
    with pytest.raises(BuiltinPolicyError, match="http"):
        await b.call("WebFetch", {"url": "file:///etc/passwd"})
    with pytest.raises(httpx.HTTPStatusError):
        await b.call("WebFetch", {"url": "http://example.test/missing"})
    await client.aclose()
