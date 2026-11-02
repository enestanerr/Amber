"""Claude Code CLI flag names and argv preparation, in one place (risk register in PLAN.md).

Verified against ``claude --help`` 2.1.316: ``--output-format stream-json`` requires
``--verbose``; ``--mcp-config`` and ``--allowedTools`` are variadic; ``-p/--print`` is a
boolean flag and the prompt is a positional argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from amber.errors import UsageError

PRINT = ("-p", "--print")
OUTPUT_FORMAT = "--output-format"
VERBOSE = "--verbose"
MCP_CONFIG = "--mcp-config"
ALLOWED_TOOLS = "--allowedTools"
PERMISSION_MODE = "--permission-mode"
STRICT_MCP_CONFIG = "--strict-mcp-config"
JSON_SCHEMA = "--json-schema"
VERSION = "--version"
STREAM_JSON = "stream-json"

# Flags that take no value (everything else `--x value` consumes one token unless listed as
# variadic). Keep in sync with `claude --help` when the CLI changes.
_BOOLEAN_FLAGS = frozenset(
    {
        *PRINT,
        VERBOSE,
        STRICT_MCP_CONFIG,
        "-c",
        "--continue",
        "--bare",
        "--restricted",
        "--include-partial-messages",
        "--allow-dangerously-skip-permissions",
        "--dangerously-skip-permissions",
        "--brief",
        "--ax-screen-reader",
        "--bg",
        "--background",
        "-d",
        "--debug",
        "-v",
        VERSION,
        "-h",
        "--help",
    }
)
_VARIADIC_FLAGS = frozenset(
    {
        MCP_CONFIG,
        ALLOWED_TOOLS,
        "--allowed-tools",
        "--add-dir",
        "--betas",
        "--tools",
        "--disallowedTools",
    }
)


@dataclass(frozen=True)
class PreparedCommand:
    argv: list[str]
    prompt: str


def _is_claude(token: str) -> bool:
    name = Path(token).name.lower()
    return name in ("claude", "claude.exe", "claude.cmd")


def _scan(args: list[str]) -> tuple[list[int], dict[str, list[str]]]:
    """Return positional token indices and a map of flag → values (``=`` form included)."""
    positionals: list[int] = []
    flags: dict[str, list[str]] = {}
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("-") and tok != "-":
            name, eq, value = tok.partition("=")
            if eq:
                flags.setdefault(name, []).append(value)
                i += 1
                continue
            values: list[str] = []
            if name in _VARIADIC_FLAGS:
                j = i + 1
                while j < len(args) and not args[j].startswith("-"):
                    values.append(args[j])
                    j += 1
                i = j
            elif name in _BOOLEAN_FLAGS:
                i += 1
            else:
                if i + 1 < len(args):
                    values.append(args[i + 1])
                i += 2
            flags.setdefault(name, []).extend(values)
        else:
            positionals.append(i)
            i += 1
    return positionals, flags


def prepare_claude_argv(
    command: list[str],
    *,
    mcp_config: Path | None = None,
    allow: list[str] | None = None,
    permission_mode: str | None = None,
    prompt_suffix: str | None = None,
) -> PreparedCommand:
    """Inject the recorder flags into a user-supplied ``claude`` command."""
    if not command or not _is_claude(command[0]):
        raise UsageError(
            "the agent command must start with `claude` (v0.1 records Claude Code only)",
            hint="Other harnesses: run them behind `amber proxy` and use `amber import --proxy`.",
        )
    args = list(command[1:])
    positionals, flags = _scan(args)
    if OUTPUT_FORMAT in flags and flags[OUTPUT_FORMAT] != [STREAM_JSON]:
        raise UsageError(
            f"conflicting {OUTPUT_FORMAT} {flags[OUTPUT_FORMAT][0]!r}; "
            f"the recorder needs {STREAM_JSON}",
            hint=f"Drop {OUTPUT_FORMAT}; amber adds "
            f"`{OUTPUT_FORMAT} {STREAM_JSON} {VERBOSE}` itself.",
        )
    if not positionals:
        raise UsageError(
            "no prompt found in the claude command",
            hint='Pass the task as a positional: `-- claude -p "Pull the orders for week ..."`.',
        )
    prompt = args[positionals[0]]
    if prompt_suffix:
        prompt = prompt + prompt_suffix
        args[positionals[0]] = prompt
    if not any(f in flags for f in PRINT):
        args.insert(0, "-p")
    if OUTPUT_FORMAT not in flags:
        args += [OUTPUT_FORMAT, STREAM_JSON]
    if VERBOSE not in flags:
        args.append(VERBOSE)
    if mcp_config is not None and MCP_CONFIG not in flags:
        args += [MCP_CONFIG, str(mcp_config)]
    if allow and ALLOWED_TOOLS not in flags and "--allowed-tools" not in flags:
        args += [ALLOWED_TOOLS, ",".join(allow)]
    if permission_mode and PERMISSION_MODE not in flags:
        args += [PERMISSION_MODE, permission_mode]
    return PreparedCommand(argv=[command[0], *args], prompt=prompt)


_MANAGED_FLAGS = frozenset(
    {*PRINT, OUTPUT_FORMAT, VERBOSE, MCP_CONFIG, ALLOWED_TOOLS, "--allowed-tools", PERMISSION_MODE}
)


def passthrough_args(command: list[str]) -> list[str]:
    """The user's own ``claude`` flags (e.g. ``--model``, ``--setting-sources``): everything after
    the executable except the prompt positional and the flags the recorder manages itself. The
    fallback relaunches the agent with these so it runs the way the recordings were made."""
    if not command:
        return []
    args = list(command[1:])
    positionals, _ = _scan(args)
    prompt_idx = positionals[0] if positionals else -1
    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if i == prompt_idx:
            i += 1
            continue
        if not (tok.startswith("-") and tok != "-"):
            out.append(tok)
            i += 1
            continue
        name, eq, _ = tok.partition("=")
        span = [tok]
        if not eq:
            if name in _VARIADIC_FLAGS:
                j = i + 1
                while j < len(args) and not args[j].startswith("-"):
                    span.append(args[j])
                    j += 1
            elif name not in _BOOLEAN_FLAGS and i + 1 < len(args):
                span.append(args[i + 1])
        if name not in _MANAGED_FLAGS:
            out.extend(span)
        i += len(span)
    return out


def inputs_suffix(inputs: dict[str, str]) -> str:
    """The line appended to the prompt so the agent sees the declared inputs."""
    if not inputs:
        return ""
    return "\nInputs: " + ", ".join(f"{k}={v}" for k, v in inputs.items())
