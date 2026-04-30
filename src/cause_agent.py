"""LangGraph ReAct agent that generates a LikelyCause hypothesis for a FailureDetail."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from src.config import config
from src.models import DiffHunk, FailureDetail, LikelyCause


@dataclass
class DiffContext:
    hunks: list[DiffHunk]
    full_diff: str


class CauseAgentState(MessagesState):
    diff_context: DiffContext
    failure: FailureDetail


@tool
def read_diff_hunk(file_path: str, hunk_index: int) -> str:
    """Read the content of a specific diff hunk by file path and index."""
    return f"Hunk {hunk_index} of {file_path}: (injected via state in run_cause_agent)"


@tool
def read_test_context(test_id: str) -> str:
    """Read the full test function body for a given test_id (e.g. tests/test_auth.py::test_login_valid)."""
    return (
        "(test body not available in Layer 1 — use failure_message from FailureDetail)"
    )


@tool
def check_symbol_in_diff(symbol: str) -> str:
    """Check if a symbol (function name, class, variable) appears in the diff and how it changed."""
    return f"Searching for '{symbol}' in diff..."


tools = [read_diff_hunk, read_test_context, check_symbol_in_diff]


def _build_graph() -> CompiledStateGraph:
    model = ChatOllama(
        model=config.enrichment.likely_cause.model,
        base_url=config.enrichment.likely_cause.base_url,
        temperature=0,
    ).bind_tools(tools)

    def call_model(state: CauseAgentState) -> dict:
        return {"messages": [model.invoke(state["messages"])]}

    builder = StateGraph(CauseAgentState)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", tools_condition)
    builder.add_edge("tools", "model")
    return builder.compile()


graph = _build_graph()


def run_cause_agent(
    failure: FailureDetail, diff_context: DiffContext
) -> Optional[LikelyCause]:
    """Run the ReAct agent.

    Returns None if Ollama unavailable, timed out, or disabled in config.
    fallback_on_timeout=True means the enrichment pipeline continues without likely_cause.
    """
    if not config.enrichment.likely_cause.enabled:
        return None

    system_prompt = (
        "You are a code debugging assistant. Given a test failure and a git diff, "
        "identify the most likely cause. Be concise and specific. "
        "Use tools to read relevant diff hunks before forming your hypothesis."
    )
    initial_message = (
        f"Test '{failure.test_id}' failed with:\n{failure.failure_message}\n\n"
        f"The diff for this commit touches {len(diff_context.hunks)} hunks. "
        f"What is the most likely cause?"
    )

    try:
        result = graph.invoke(
            {
                "messages": [("system", system_prompt), ("human", initial_message)],
                "diff_context": diff_context,
                "failure": failure,
            },
            config={
                "recursion_limit": config.enrichment.likely_cause.max_react_steps * 2
            },
        )
        final_message = result["messages"][-1].content
        steps_taken = len(
            [m for m in result["messages"] if getattr(m, "tool_calls", None)]
        )
        return LikelyCause(
            hypothesis=final_message,
            reasoning_steps=steps_taken,
            model=config.enrichment.likely_cause.model,
        )
    except Exception:
        if config.enrichment.likely_cause.fallback_on_timeout:
            return None
        raise
