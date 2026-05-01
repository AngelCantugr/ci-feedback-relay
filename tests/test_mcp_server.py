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


# ---------------------------------------------------------------------------
# list_tools: exposes get_branch_context and get_review_comments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_contains_all_four_tools(db):
    """list_tools must expose all four tools."""
    from src.mcp_server import list_tools

    tools = await list_tools()
    names = [t.name for t in tools]
    assert "get_ci_failure_context" in names
    assert "get_branch_context" in names
    assert "get_review_comments" in names
    assert "register_branch_watch" in names


# ---------------------------------------------------------------------------
# _get_branch_context: nonexistent branch → BranchContextPayload(ci_status="none")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_branch_context_nonexistent_branch(db, monkeypatch):
    """get_branch_context must return BranchContextPayload(ci_status='none') for a missing branch."""
    from unittest.mock import MagicMock

    mock_repo = MagicMock()
    mock_repo.get_branch.side_effect = Exception("Branch not found")
    mock_github = MagicMock()
    mock_github.get_repo.return_value = mock_repo

    import src.github_client as gc_mod

    monkeypatch.setattr(gc_mod, "get_github_client", lambda: mock_github)

    from src.mcp_server import _get_branch_context

    result = await _get_branch_context("nonexistent-branch")

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["ci_status"] == "none"
    assert data["branch"] == "nonexistent-branch"
    assert data["event_type"] == "branch_context"


# ---------------------------------------------------------------------------
# call_tool: routes to _get_branch_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_get_branch_context_nonexistent(db, monkeypatch):
    """call_tool('get_branch_context', ...) must return BranchContextPayload for missing branch."""
    from unittest.mock import MagicMock

    mock_repo = MagicMock()
    mock_repo.get_branch.side_effect = Exception("Branch not found")
    mock_github = MagicMock()
    mock_github.get_repo.return_value = mock_repo

    import src.github_client as gc_mod

    monkeypatch.setattr(gc_mod, "get_github_client", lambda: mock_github)

    from src.mcp_server import call_tool

    result = await call_tool("get_branch_context", {"branch": "no-such-branch"})

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["ci_status"] == "none"


# ---------------------------------------------------------------------------
# _get_review_comments: no rows → empty blocking_comments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_review_comments_empty(db):
    """get_review_comments(999) must return empty blocking_comments, not raise."""
    from src.mcp_server import _get_review_comments

    result = await _get_review_comments(999)

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["pr_number"] == 999
    assert data["blocking_comments"] == []
    assert data["total_comments"] == 0
    assert data["event_type"] == "review_comments"


# ---------------------------------------------------------------------------
# _get_review_comments: author_type is populated from SQLite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_review_comments_with_blocking_comment(db):
    """get_review_comments must return blocking comments with author_type from SQLite."""
    db.store_review_comment(
        pr_number=42,
        repo="angelcantugr/ci-feedback-relay",
        comment_id=1001,
        author="alice",
        author_type="human_review",
        body="Please fix this.",
        file_path="src/foo.py",
        line=10,
        is_blocking=True,
    )
    db.store_review_comment(
        pr_number=42,
        repo="angelcantugr/ci-feedback-relay",
        comment_id=1002,
        author="coderabbit[bot]",
        author_type="ai_review",
        body="Minor suggestion.",
        is_blocking=False,
    )

    from src.mcp_server import _get_review_comments

    result = await _get_review_comments(42)

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["pr_number"] == 42
    assert data["total_comments"] == 2
    assert len(data["blocking_comments"]) == 1
    comment = data["blocking_comments"][0]
    assert comment["author_type"] == "human_review"
    assert comment["author"] == "alice"
    assert comment["is_blocking"] is True


# ---------------------------------------------------------------------------
# call_tool: routes to _get_review_comments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_get_review_comments(db):
    """call_tool('get_review_comments', ...) must delegate to _get_review_comments."""
    from src.mcp_server import call_tool

    result = await call_tool("get_review_comments", {"pr_number": 999})

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["blocking_comments"] == []


# ---------------------------------------------------------------------------
# register_branch_watch tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_branch_watch_success(db):
    """register_branch_watch must return ok=True with branch and session_id."""
    from src.mcp_server import _register_branch_watch

    result = await _register_branch_watch(
        {"branch": "feature/new", "session_id": "ses-1"}
    )

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["ok"] is True
    assert data["branch"] == "feature/new"
    assert data["session_id"] == "ses-1"


@pytest.mark.asyncio
async def test_register_branch_watch_default_session(db):
    """register_branch_watch without session_id must default to 'default'."""
    from src.mcp_server import _register_branch_watch

    result = await _register_branch_watch({"branch": "main"})

    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["ok"] is True
    assert data["branch"] == "main"
    assert data["session_id"] == "default"
