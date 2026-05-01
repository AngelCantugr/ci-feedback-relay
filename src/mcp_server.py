"""MCP server: stdio transport exposing enriched CI failure data to Claude Code sessions."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src.db import get_enriched_failure
from src.db import get_review_comments as db_get_review_comments
from src.models import BranchContextPayload, ReviewComment, ReviewCommentsPayload

server = Server("ci-feedback-relay")


async def _drain_channel() -> None:
    """Background task: drain channel queue and emit events to stderr."""
    import json
    import sys

    from src.channel import _channel_queue

    while True:
        event = await _channel_queue.get()
        try:
            print(json.dumps(event), file=sys.stderr)
        except Exception as exc:
            print(f"channel drain error: {exc!r}", file=sys.stderr)
        finally:
            _channel_queue.task_done()


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_ci_failure_context",
            description=(
                "Retrieve the enriched CI failure context for a given commit SHA. "
                "Returns structured failure details, diff hunks, likely cause hypothesis, "
                "circuit breaker state, and the recommended response action. "
                "Call this after a CI failure to understand what broke and why."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sha": {
                        "type": "string",
                        "description": "The 40-character commit SHA that triggered the CI failure",
                    }
                },
                "required": ["sha"],
            },
        ),
        types.Tool(
            name="get_branch_context",
            description=(
                "Get the current state of a branch: HEAD SHA, commits ahead of base, "
                "CI status, open PR number, and whether there is a merge conflict. "
                "Use this to orient before making changes on the branch."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Branch name (e.g. feature/my-fix)",
                    }
                },
                "required": ["branch"],
            },
        ),
        types.Tool(
            name="get_review_comments",
            description=(
                "Get blocking review comments for a pull request, filtered to is_blocking=true. "
                "Each comment includes author_type (human_review | ai_review | ci_automated | agent_self) "
                "so the agent can focus on human feedback and ignore bot noise."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pr_number": {"type": "integer", "description": "GitHub PR number"}
                },
                "required": ["pr_number"],
            },
        ),
        types.Tool(
            name="register_branch_watch",
            description=(
                "Register a branch for CI failure monitoring. "
                "Called automatically by the PostToolUse hook after a git push. "
                "Stores (branch → session_id) mapping so channel pushes are routed correctly. "
                "Layer 1: single session — included for Layer 2 routing upgrade path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "branch": {"type": "string"},
                    "session_id": {"type": "string", "default": "default"},
                },
                "required": ["branch"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "get_ci_failure_context":
        return await _get_ci_failure_context(arguments["sha"])
    if name == "get_branch_context":
        return await _get_branch_context(arguments["branch"])
    if name == "get_review_comments":
        return await _get_review_comments(int(arguments["pr_number"]))
    if name == "register_branch_watch":
        return await _register_branch_watch(arguments)
    raise ValueError(f"Unknown tool: {name}")


async def _get_ci_failure_context(sha: str) -> list[types.TextContent]:
    payload = get_enriched_failure(sha)
    if payload is None:
        return [
            types.TextContent(
                type="text",
                text=json.dumps({"error": f"No enriched failure found for sha: {sha}"}),
            )
        ]
    return [types.TextContent(type="text", text=json.dumps(payload))]


async def _get_branch_context(branch: str) -> list[types.TextContent]:
    from src.github_client import get_github_client

    g = get_github_client()
    repo = g.get_repo("angelcantugr/ci-feedback-relay")  # Layer 1: single repo

    try:
        br = repo.get_branch(branch)
        head_sha = br.commit.sha
    except Exception:
        payload = BranchContextPayload(branch=branch, ci_status="none")
        return [types.TextContent(type="text", text=json.dumps(asdict(payload)))]

    commit = repo.get_commit(head_sha)
    statuses = list(commit.get_statuses())
    ci_status = statuses[0].state if statuses else "none"

    pulls = list(repo.get_pulls(state="open", head=f"angelcantugr:{branch}"))
    pr_number = pulls[0].number if pulls else None

    comparison = repo.compare("main", branch)
    payload = BranchContextPayload(
        branch=branch,
        head_sha=head_sha,
        commits_ahead_of_base=comparison.ahead_by,
        base_branch="main",
        ci_status=ci_status,
        open_pr_number=pr_number,
        has_merge_conflict=(comparison.status == "diverged"),
    )
    return [types.TextContent(type="text", text=json.dumps(asdict(payload)))]


async def _get_review_comments(pr_number: int) -> list[types.TextContent]:
    rows = db_get_review_comments(pr_number, "angelcantugr/ci-feedback-relay")
    blocking = [
        ReviewComment(
            comment_id=r["comment_id"],
            body=r["body"],
            author=r["author"],
            author_type=r["author_type"],
            file_path=r["file_path"],
            line=r["line"],
            is_blocking=bool(r["is_blocking"]),
        )
        for r in rows
        if r["is_blocking"]
    ]
    payload = ReviewCommentsPayload(
        pr_number=pr_number,
        blocking_comments=blocking,
        total_comments=len(rows),
    )
    return [types.TextContent(type="text", text=json.dumps(asdict(payload)))]


async def _register_branch_watch(arguments: dict) -> list[types.TextContent]:
    from src.db import store_branch_watch

    branch = arguments["branch"]
    session_id = arguments.get("session_id", "default")
    store_branch_watch(branch, session_id)
    result = json.dumps({"ok": True, "branch": branch, "session_id": session_id})
    return [types.TextContent(type="text", text=result)]


async def main() -> None:
    drain_task = asyncio.create_task(_drain_channel())
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass  # expected on clean shutdown


if __name__ == "__main__":
    asyncio.run(main())
