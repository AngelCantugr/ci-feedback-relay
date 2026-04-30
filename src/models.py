"""Shared type contract: enums and per-failure dataclasses used across all pipeline modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FailureCategory(str, Enum):
    ASSERTION = "assertion"
    IMPORT_ERROR = "import_error"
    TIMEOUT = "timeout"
    INFRA = "infra"
    FLAKE_SUSPECTED = "flake_suspected"


class RecommendedResponse(str, Enum):
    SELF_CORRECT = "self_correct"
    RERUN = "rerun"
    ESCALATE = "escalate_to_human"
    IGNORE = "ignore"


@dataclass
class DiffHunk:
    file_path: str
    hunk_header: str  # @@ -L,S +L,S @@
    content: str
    change_type: str  # added | removed | modified


@dataclass
class LikelyCause:
    hypothesis: str  # e.g. "assertUserExists renamed in utils.py but test not updated"
    reasoning_steps: int  # ReAct iterations that ran
    model: str  # e.g. "deepseek-coder-v2"


@dataclass
class FailureDetail:
    test_id: str  # e.g. "tests/test_auth.py::test_login_valid"
    category: FailureCategory
    was_passing_on_base: bool  # True = this is a regression
    relevant_diff_hunks: list[DiffHunk]
    failure_message: str  # truncated raw output
    test_body: str | None = None  # test function source, configurable max_lines
    likely_cause: LikelyCause | None = None  # None if Ollama unavailable/timed out


@dataclass
class FailureSummary:
    total_failed: int
    total_reported: int  # min(total_failed, max_actionable_failures)
    check_run_name: str  # e.g. "CI / test (ubuntu-latest)"
    conclusion: str  # failure | timed_out
    regressions_only: bool  # True if every reported failure is a regression


@dataclass
class CircuitBreakerState:
    attempt_count: int
    max_attempts: int
    tripped: bool  # True = escalate_to_human, no further channel pushes


@dataclass
class CIFailurePayload:
    event_type: str = "ci_failure"
    sha: str = ""
    branch: str = ""
    pr_number: int | None = None
    failure_summary: FailureSummary = field(
        default_factory=lambda: FailureSummary(0, 0, "", "failure", False)
    )
    actionable_failures: list[FailureDetail] = field(default_factory=list)
    circuit_breaker: CircuitBreakerState = field(
        default_factory=lambda: CircuitBreakerState(0, 3, False)
    )
    recommended_response: RecommendedResponse = RecommendedResponse.SELF_CORRECT


@dataclass
class BranchContextPayload:
    event_type: str = "branch_context"
    branch: str = ""
    head_sha: str = ""
    commits_ahead_of_base: int = 0
    base_branch: str = "main"
    ci_status: str = ""  # pending | success | failure | none
    open_pr_number: int | None = None
    has_merge_conflict: bool = False


@dataclass
class ReviewComment:
    comment_id: int
    body: str
    author: str
    author_type: str  # human_review | ai_review | ci_automated | agent_self
    file_path: str | None = None
    line: int | None = None
    is_blocking: bool = False


@dataclass
class ReviewCommentsPayload:
    event_type: str = "review_comments"
    pr_number: int = 0
    blocking_comments: list[ReviewComment] = field(default_factory=list)
    total_comments: int = 0
