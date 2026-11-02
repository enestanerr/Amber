"""Safe subset of harness builtins for compiled programs (``docs/05-runtime.md`` §2.2).

``builtin.Read`` · ``builtin.Write`` · ``builtin.Glob`` · ``builtin.Grep`` · ``builtin.WebFetch`` ·
``builtin.Bash`` (only the recorded command template, deny-list, pinned cwd, scrubbed env).
Paths must resolve inside ``workdir`` or a configured read root.
"""

from __future__ import annotations

import html
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from amber.config import BuiltinsCfg
from amber.errors import AmberError

SUPPORTED = frozenset({"Read", "Write", "Glob", "Grep", "WebFetch", "Bash"})
WEBFETCH_MAX_BYTES = 10 * 1024 * 1024
WEBFETCH_TIMEOUT = 30.0
BASH_TIMEOUT = 120.0
GREP_MAX_HITS = 200
BASH_DENY = [
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bsudo\b"),
    re.compile(r"curl[^|]*\|\s*(ba)?sh"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b"),
]
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.DOTALL | re.IGNORECASE)


class BuiltinPolicyError(AmberError):
    exit_code = 4


class BuiltinBackend:
    def __init__(
        self,
        workdir: Path,
        cfg: BuiltinsCfg | None = None,
        *,
        bash_templates: dict[int, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.workdir = workdir.resolve()
        self.cfg = cfg or BuiltinsCfg()
        self.roots = [self.workdir, *[Path(r).resolve() for r in self.cfg.read_roots]]
        self.bash_templates = dict(bash_templates or {})
        self._http = http_client

    # -- helpers ------------------------------------------------------------
    def resolve(self, raw: str, *, write: bool = False) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            path = self.workdir / path
        path = path.resolve()
        roots = [self.workdir] if write else self.roots
        if not any(path == r or r in path.parents for r in roots):
            raise BuiltinPolicyError(
                f"path {raw!r} is outside the program's work directory",
                hint="Add the directory to [builtins].read_roots in amber.toml if intended.",
            )
        return path

    @staticmethod
    def _text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    # -- tools --------------------------------------------------------------
    async def call(self, name: str, args: dict[str, Any], *, step_idx: int = -1) -> Any:
        if name not in SUPPORTED:
            raise BuiltinPolicyError(
                f"builtin.{name} cannot be run by compiled programs",
                hint="Re-record with MCP tools or mark the step as judgment.",
            )
        if name == "Read":
            return self.read(str(args["file_path"]), args.get("offset"), args.get("limit"))
        if name == "Write":
            return self.write(str(args["file_path"]), str(args.get("content", "")))
        if name == "Glob":
            return self.glob(str(args["pattern"]), args.get("path"))
        if name == "Grep":
            return self.grep(str(args["pattern"]), args.get("path"), args.get("glob"))
        if name == "WebFetch":
            return await self.webfetch(str(args["url"]))
        return self.bash(str(args["command"]), step_idx=step_idx)

    def read(self, file_path: str, offset: Any = None, limit: Any = None) -> str:
        path = self.resolve(file_path)
        lines = self._text(path).splitlines(keepends=True)
        start = int(offset) if offset else 0
        end = start + int(limit) if limit else None
        return "".join(lines[start:end])

    def write(self, file_path: str, content: str) -> dict[str, Any]:
        path = self.resolve(file_path, write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(path), "bytes": len(content.encode("utf-8"))}

    def glob(self, pattern: str, path: Any = None) -> list[str]:
        base = self.resolve(str(path)) if path else self.workdir
        hits = sorted(str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file())
        return hits

    def grep(self, pattern: str, path: Any = None, glob: Any = None) -> list[dict[str, Any]]:
        base = self.resolve(str(path)) if path else self.workdir
        regex = re.compile(pattern)
        files = (
            [base]
            if base.is_file()
            else sorted(p for p in base.rglob(str(glob) if glob else "*") if p.is_file())
        )
        hits: list[dict[str, Any]] = []
        for file in files:
            try:
                text = self._text(file)
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(
                        {
                            "file": str(file.relative_to(base) if file != base else file.name),
                            "line": n,
                            "text": line[:300],
                        }
                    )
                    if len(hits) >= GREP_MAX_HITS:
                        return hits
        return hits

    async def webfetch(self, url: str) -> str:
        if not url.lower().startswith(("http://", "https://")):
            raise BuiltinPolicyError(f"WebFetch only supports http(s) URLs, got {url!r}")
        client = self._http or httpx.AsyncClient(follow_redirects=True, timeout=WEBFETCH_TIMEOUT)
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > WEBFETCH_MAX_BYTES:
                        raise BuiltinPolicyError(
                            f"WebFetch response exceeds {WEBFETCH_MAX_BYTES} bytes"
                        )
                    chunks.append(chunk)
                content_type = response.headers.get("content-type", "")
        finally:
            if self._http is None:
                await client.aclose()
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if "html" in content_type.lower():
            text = html.unescape(_TAG_RE.sub(" ", text))
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n", text).strip()
        return text

    def bash(self, command: str, *, step_idx: int) -> str:
        template = self.bash_templates.get(step_idx)
        if template is None or not re.fullmatch(template, command, re.DOTALL):
            raise BuiltinPolicyError(
                "Bash command does not match the recorded template for this step",
                hint="Compiled programs may only run recorded commands (with inputs substituted).",
            )
        for pattern in BASH_DENY:
            if pattern.search(command):
                raise BuiltinPolicyError(f"Bash command matches the deny-list ({pattern.pattern})")
        if "|" in command and "|" not in template:
            raise BuiltinPolicyError("Bash pipes are allowed only when the recording used them")
        env = {
            k: os.environ[k]
            for k in ("PATH", "HOME", "LANG", "SYSTEMROOT", "TEMP", "TMP")
            if k in os.environ
        }
        env.update(self.cfg.bash_env)
        proc = subprocess.run(
            # Windows needs the raw string (CreateProcess parses it); POSIX gets an argv list.
            command if sys.platform == "win32" else shlex.split(command),
            cwd=self.workdir,
            timeout=BASH_TIMEOUT,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        out = proc.stdout
        if proc.stderr.strip():
            out += ("\n" if out and not out.endswith("\n") else "") + proc.stderr
        if proc.returncode != 0:
            out += f"\n[exit code {proc.returncode}]"
        return out
