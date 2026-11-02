"""Mock GitHub MCP server for the issue-triage example (``docs/06-examples.md`` §2).

Tools: ``list_issues``, ``get_issue``, ``search_issues`` (read) and the batch writes
``add_labels(updates)``, ``add_comments(comments)``. Batches, because Amber v0.1 does not
collapse per-item loops: one write per run compiles to one step whose list argument is filled
by a single hole. Data: 40 seeded synthetic issues across 6 areas with 4 duplicate pairs
(``scenario.py``). Scenario ``B`` switches the label taxonomy from ``area:*`` to ``kind/*`` and
``add_labels`` rejects unknown labels → the compiled program trips the error guard and the
agent has to re-derive the labels.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_shared"))

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp_common import scenario
from pydantic import BaseModel, Field
from scenario import AREAS, issues

mcp = MCPServer("github")
READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
SCENARIO = scenario()
ISSUES: dict[int, dict[str, Any]] = {i["number"]: i for i in issues()}
COMMENTS: list[dict[str, Any]] = []


class LabelUpdate(BaseModel):
    number: int = Field(description="Issue number")
    labels: list[str] = Field(description="Labels to add to that issue")


class NewComment(BaseModel):
    number: int = Field(description="Issue number")
    body: str = Field(description="Comment text")


def known_labels() -> list[str]:
    prefix = "kind/" if SCENARIO == "B" else "area:"
    return [f"{prefix}{a}" for a in AREAS] + ["duplicate", "needs-info"]


@mcp.tool(annotations=READ)
def list_issues(state: str = "open", since: str | None = None) -> list[dict[str, Any]]:
    """Issues in a state (open|closed|all), optionally created on/after an ISO date."""
    out: list[dict[str, Any]] = []
    for issue in ISSUES.values():
        if state != "all" and issue["state"] != state:
            continue
        if since and issue["created_at"] < since:
            continue
        out.append({k: issue[k] for k in ("number", "title", "body", "labels", "created_at")})
    return sorted(out, key=lambda i: i["number"])


@mcp.tool(annotations=READ)
def get_issue(number: int) -> dict[str, Any]:
    """One issue with its comments."""
    issue = ISSUES.get(number)
    if issue is None:
        raise ValueError(f"no issue #{number}")
    return {**issue, "comments": [c for c in COMMENTS if c["number"] == number]}


@mcp.tool(annotations=READ)
def search_issues(query: str) -> list[dict[str, Any]]:
    """Case-insensitive title/body search."""
    q = query.lower()
    return [
        {"number": i["number"], "title": i["title"]}
        for i in sorted(ISSUES.values(), key=lambda i: i["number"])
        if q in i["title"].lower() or q in i["body"].lower()
    ]


@mcp.tool(annotations=WRITE)
def add_labels(updates: list[LabelUpdate]) -> dict[str, Any]:
    """Add labels to several issues in one call, e.g. ``[{"number": 7, "labels": ["area:api"]}]``.

    All or nothing: the batch is rejected if an issue or a label is unknown (the label taxonomy
    changes in scenario B).
    """
    for u in updates:
        if u.number not in ISSUES:
            raise ValueError(f"no issue #{u.number}")
        unknown = [lbl for lbl in u.labels if lbl not in known_labels()]
        if unknown:
            raise ValueError(f"unknown labels {unknown} on #{u.number}; known: {known_labels()}")
    for u in updates:
        issue = ISSUES[u.number]
        for lbl in u.labels:
            if lbl not in issue["labels"]:
                issue["labels"].append(lbl)
    return {"ok": True, "updated": [u.number for u in updates]}


@mcp.tool(annotations=WRITE)
def add_comments(comments: list[NewComment]) -> dict[str, Any]:
    """Comment on several issues in one call.

    Example: ``[{"number": 31, "body": "Duplicate of #12"}]``.
    """
    for c in comments:
        if c.number not in ISSUES:
            raise ValueError(f"no issue #{c.number}")
    ids: list[int] = []
    for c in comments:
        comment = {"id": 1000 + len(COMMENTS) + 1, "number": c.number, "body": c.body}
        COMMENTS.append(comment)
        ids.append(comment["id"])
    return {"ok": True, "ids": ids}


if __name__ == "__main__":
    mcp.run(transport="stdio")
