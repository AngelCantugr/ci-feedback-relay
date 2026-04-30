"""Tests for src/mcp_server.py — MCP server scaffold and get_ci_failure_context tool."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated temp DB for each test."""
    db_file = str(tmp_path / "test.db")
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", db_file)

    import importlib

    import src.db as db_mod

    importlib.reload(db_mod)
    db_mod.init_db()
    return db_mod


# ---------------------------------------------------------------------------
# _get_ci_failure_context: nonexistent SHA → error JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ci_failure_context_nonexistent_sha(db):
    """get_ci_failure_context must return error JSON for an unknown SHA, not raise."""
    from src.mcp_server import _get_ci_failure_context

    result = await _get_ci_failure_context("nonexistent-sha")

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert "error" in data
    assert "nonexistent-sha" in data["error"]


# ---------------------------------------------------------------------------
# _get_ci_failure_context: valid SHA → stored payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ci_failure_context_valid_sha(db):
    """get_ci_failure_context must return the JSON-serialised CIFailurePayload for a known SHA."""
    from src.mcp_server import _get_ci_failure_context

    stored = {
        "event_type": "ci_failure",
        "sha": "abc123def456",
        "branch": "main",
        "actionable_failures": [],
        "recommended_response": "self_correct",
    }
    db.store_enriched_failure("abc123def456", "main", "org/repo", None, 1, "CI", stored)

    result = await _get_ci_failure_context("abc123def456")

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["sha"] == "abc123def456"
    assert data["event_type"] == "ci_failure"
    assert data["recommended_response"] == "self_correct"


# ---------------------------------------------------------------------------
# call_tool: routes to _get_ci_failure_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_get_ci_failure_context(db):
    """call_tool('get_ci_failure_context', ...) must delegate to _get_ci_failure_context."""
    from src.mcp_server import call_tool

    result = await call_tool("get_ci_failure_context", {"sha": "missing-sha"})

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert "error" in data


# ---------------------------------------------------------------------------
# call_tool: unknown tool name → ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_unknown_raises(db):
    """call_tool with an unknown name must raise ValueError."""
    from src.mcp_server import call_tool

    with pytest.raises(ValueError, match="Unknown tool"):
        await call_tool("no_such_tool", {})


# ---------------------------------------------------------------------------
# list_tools: exposes get_ci_failure_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_contains_get_ci_failure_context(db):
    """list_tools must include a tool named get_ci_failure_context."""
    from src.mcp_server import list_tools

    tools = await list_tools()
    names = [t.name for t in tools]
    assert "get_ci_failure_context" in names


@pytest.mark.asyncio
async def test_list_tools_schema_has_sha_required(db):
    """The get_ci_failure_context tool schema must require a 'sha' parameter."""
    from src.mcp_server import list_tools

    tools = await list_tools()
    tool = next(t for t in tools if t.name == "get_ci_failure_context")
    assert "sha" in tool.inputSchema["properties"]
    assert "sha" in tool.inputSchema["required"]
