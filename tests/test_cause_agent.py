"""Tests for src/cause_agent.py — LangGraph ReAct agent with Ollama timeout fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_failure(test_id: str = "tests/test_foo.py::test_bar"):
    from src.models import FailureCategory, FailureDetail

    return FailureDetail(
        test_id=test_id,
        category=FailureCategory.ASSERTION,
        was_passing_on_base=True,
        relevant_diff_hunks=[],
        failure_message="AssertionError: expected True got False",
    )


def _make_diff_context(num_hunks: int = 2):
    from src.cause_agent import DiffContext
    from src.models import DiffHunk

    hunks = [
        DiffHunk(
            file_path=f"src/file_{i}.py",
            hunk_header=f"@@ -{i},3 +{i},4 @@",
            content=f"-old line {i}\n+new line {i}",
            change_type="modified",
        )
        for i in range(num_hunks)
    ]
    return DiffContext(hunks=hunks, full_diff="--- a/src/file.py\n+++ b/src/file.py")


def _make_ai_message(content: str, tool_calls: list | None = None) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    if tool_calls is not None:
        msg.tool_calls = tool_calls
    else:
        # Empty list mimics a plain AI message (no tool calls)
        msg.tool_calls = []
    return msg


# ---------------------------------------------------------------------------
# enabled=False short-circuits immediately
# ---------------------------------------------------------------------------


def test_run_cause_agent_disabled_returns_none(monkeypatch):
    """When likely_cause.enabled is False, returns None without invoking graph."""
    from src.cause_agent import run_cause_agent

    monkeypatch.setattr("src.cause_agent.config.enrichment.likely_cause.enabled", False)

    result = run_cause_agent(_make_failure(), _make_diff_context())

    assert result is None


# ---------------------------------------------------------------------------
# Ollama unavailable → fallback_on_timeout=True returns None
# ---------------------------------------------------------------------------


def test_run_cause_agent_ollama_unavailable_fallback(monkeypatch):
    """Returns None (not an exception) when Ollama is not running and fallback_on_timeout=True."""
    from src.cause_agent import run_cause_agent

    monkeypatch.setattr("src.cause_agent.config.enrichment.likely_cause.enabled", True)
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.fallback_on_timeout", True
    )

    with patch("src.cause_agent.graph") as mock_graph:
        mock_graph.invoke.side_effect = ConnectionRefusedError("Ollama not running")
        result = run_cause_agent(_make_failure(), _make_diff_context())

    assert result is None


def test_run_cause_agent_ollama_timeout_fallback(monkeypatch):
    """Returns None when Ollama times out and fallback_on_timeout=True."""
    from src.cause_agent import run_cause_agent

    monkeypatch.setattr("src.cause_agent.config.enrichment.likely_cause.enabled", True)
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.fallback_on_timeout", True
    )

    with patch("src.cause_agent.graph") as mock_graph:
        mock_graph.invoke.side_effect = TimeoutError("Ollama timed out")
        result = run_cause_agent(_make_failure(), _make_diff_context())

    assert result is None


# ---------------------------------------------------------------------------
# fallback_on_timeout=False re-raises
# ---------------------------------------------------------------------------


def test_run_cause_agent_no_fallback_raises(monkeypatch):
    """Re-raises exception when fallback_on_timeout=False."""
    from src.cause_agent import run_cause_agent

    monkeypatch.setattr("src.cause_agent.config.enrichment.likely_cause.enabled", True)
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.fallback_on_timeout", False
    )

    with patch("src.cause_agent.graph") as mock_graph:
        mock_graph.invoke.side_effect = RuntimeError("unexpected error")
        with pytest.raises(RuntimeError, match="unexpected error"):
            run_cause_agent(_make_failure(), _make_diff_context())


# ---------------------------------------------------------------------------
# Successful Ollama response → LikelyCause returned
# ---------------------------------------------------------------------------


def test_run_cause_agent_returns_likely_cause(monkeypatch):
    """Returns LikelyCause with non-empty hypothesis when Ollama responds."""
    from src.cause_agent import run_cause_agent
    from src.models import LikelyCause

    monkeypatch.setattr("src.cause_agent.config.enrichment.likely_cause.enabled", True)
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.model", "deepseek-coder-v2"
    )
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.fallback_on_timeout", True
    )

    # Two messages: one with tool_calls (ReAct step) + final answer
    tool_msg = _make_ai_message("(tool call)", tool_calls=[{"name": "read_diff_hunk"}])
    final_msg = _make_ai_message("The test broke because assertUserExists was renamed.")

    with patch("src.cause_agent.graph") as mock_graph:
        mock_graph.invoke.return_value = {"messages": [tool_msg, final_msg]}
        result = run_cause_agent(_make_failure(), _make_diff_context())

    assert isinstance(result, LikelyCause)
    assert result.hypothesis == "The test broke because assertUserExists was renamed."
    assert result.model == "deepseek-coder-v2"


def test_run_cause_agent_reasoning_steps_count(monkeypatch):
    """reasoning_steps reflects actual number of messages that have tool_calls."""
    from src.cause_agent import run_cause_agent

    monkeypatch.setattr("src.cause_agent.config.enrichment.likely_cause.enabled", True)
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.model", "deepseek-coder-v2"
    )
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.fallback_on_timeout", True
    )

    # 3 tool-calling messages + 1 final answer = steps_taken should be 3
    tool_msgs = [
        _make_ai_message(f"step {i}", tool_calls=[{"name": "read_diff_hunk"}])
        for i in range(3)
    ]
    final_msg = _make_ai_message("Root cause identified.")

    with patch("src.cause_agent.graph") as mock_graph:
        mock_graph.invoke.return_value = {"messages": tool_msgs + [final_msg]}
        result = run_cause_agent(_make_failure(), _make_diff_context())

    assert result is not None
    assert result.reasoning_steps == 3


# ---------------------------------------------------------------------------
# recursion_limit is set to max_react_steps * 2
# ---------------------------------------------------------------------------


def test_run_cause_agent_recursion_limit(monkeypatch):
    """graph.invoke is called with recursion_limit == max_react_steps * 2."""
    from src.cause_agent import run_cause_agent

    monkeypatch.setattr("src.cause_agent.config.enrichment.likely_cause.enabled", True)
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.max_react_steps", 4
    )
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.model", "deepseek-coder-v2"
    )
    monkeypatch.setattr(
        "src.cause_agent.config.enrichment.likely_cause.fallback_on_timeout", True
    )

    final_msg = _make_ai_message("Some hypothesis.")

    with patch("src.cause_agent.graph") as mock_graph:
        mock_graph.invoke.return_value = {"messages": [final_msg]}
        run_cause_agent(_make_failure(), _make_diff_context())

        call_kwargs = mock_graph.invoke.call_args
        assert call_kwargs is not None
        # config kwarg is passed as keyword argument
        invoke_config = call_kwargs.kwargs.get("config") or call_kwargs.args[1]
        assert invoke_config["recursion_limit"] == 8  # 4 * 2
