"""MCP server: stdio transport exposing enriched CI failure data to Claude Code sessions."""

from __future__ import annotations

import asyncio
import json

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src.db import get_enriched_failure

server = Server("ci-feedback-relay")


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
        # get_branch_context and get_review_comments added in Issue AngelCantugr/ci-feedback-relay#28
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "get_ci_failure_context":
        return await _get_ci_failure_context(arguments["sha"])
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


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
