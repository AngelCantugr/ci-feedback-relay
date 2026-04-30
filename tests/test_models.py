"""Tests for src/models.py — core enums and dataclasses."""

from __future__ import annotations

import dataclasses

from src.models import (
    BranchContextPayload,
    CIFailurePayload,
    CircuitBreakerState,
    DiffHunk,
    FailureCategory,
    FailureDetail,
    FailureSummary,
    LikelyCause,
    RecommendedResponse,
    ReviewComment,
    ReviewCommentsPayload,
)


def test_failure_category_is_str_enum():
    """FailureCategory values are plain strings (JSON-serializable without custom encoder)."""
    assert FailureCategory.ASSERTION == "assertion"
    assert isinstance(FailureCategory.ASSERTION, str)


def test_failure_category_all_values():
    values = {c.value for c in FailureCategory}
    assert values == {
        "assertion",
        "import_error",
        "timeout",
        "infra",
        "flake_suspected",
    }


def test_recommended_response_is_str_enum():
    """RecommendedResponse values are plain strings."""
    assert RecommendedResponse.ESCALATE == "escalate_to_human"
    assert isinstance(RecommendedResponse.ESCALATE, str)


def test_recommended_response_all_values():
    values = {r.value for r in RecommendedResponse}
    assert values == {"self_correct", "rerun", "escalate_to_human", "ignore"}


def test_diff_hunk_fields():
    hunk = DiffHunk(
        file_path="src/utils.py",
        hunk_header="@@ -1,3 +1,4 @@",
        content="+import os",
        change_type="added",
    )
    assert hunk.file_path == "src/utils.py"
    assert hunk.hunk_header == "@@ -1,3 +1,4 @@"
    assert hunk.content == "+import os"
    assert hunk.change_type == "added"


def test_likely_cause_fields():
    cause = LikelyCause(
        hypothesis="assertUserExists renamed in utils.py but test not updated",
        reasoning_steps=3,
        model="deepseek-coder-v2",
    )
    assert cause.hypothesis.startswith("assertUserExists")
    assert cause.reasoning_steps == 3
    assert cause.model == "deepseek-coder-v2"


def test_failure_detail_required_fields():
    """was_passing_on_base is a non-optional bool."""
    hunk = DiffHunk("f.py", "@@ -1 +1 @@", "-old\n+new", "modified")
    detail = FailureDetail(
        test_id="tests/test_auth.py::test_login_valid",
        category=FailureCategory.ASSERTION,
        was_passing_on_base=True,
        relevant_diff_hunks=[hunk],
        failure_message="AssertionError: expected True",
    )
    assert detail.was_passing_on_base is True
    assert isinstance(detail.was_passing_on_base, bool)


def test_failure_detail_likely_cause_defaults_none():
    """likely_cause defaults to None when Ollama is unavailable."""
    detail = FailureDetail(
        test_id="tests/test_foo.py::test_bar",
        category=FailureCategory.FLAKE_SUSPECTED,
        was_passing_on_base=False,
        relevant_diff_hunks=[],
        failure_message="Flaky timeout",
    )
    assert detail.likely_cause is None
    assert detail.test_body is None


def test_failure_detail_with_likely_cause():
    cause = LikelyCause("hypothesis text", 2, "deepseek-coder-v2")
    detail = FailureDetail(
        test_id="tests/test_db.py::test_insert",
        category=FailureCategory.IMPORT_ERROR,
        was_passing_on_base=True,
        relevant_diff_hunks=[],
        failure_message="ModuleNotFoundError: No module named 'src.db'",
        likely_cause=cause,
    )
    assert detail.likely_cause is cause
    assert detail.likely_cause.reasoning_steps == 2


def test_failure_detail_is_dataclass():
    """dataclasses.asdict() serializes FailureDetail without errors."""
    hunk = DiffHunk("a.py", "@@ -1 +1 @@", "+x = 1", "added")
    detail = FailureDetail(
        test_id="tests/t.py::t",
        category=FailureCategory.INFRA,
        was_passing_on_base=False,
        relevant_diff_hunks=[hunk],
        failure_message="Runner OOM",
    )
    d = dataclasses.asdict(detail)
    assert d["category"] == "infra"
    assert d["likely_cause"] is None
    assert d["relevant_diff_hunks"][0]["change_type"] == "added"


def test_failure_summary_fields():
    summary = FailureSummary(
        total_failed=5,
        total_reported=3,
        check_run_name="CI / test (ubuntu-latest)",
        conclusion="failure",
        regressions_only=True,
    )
    assert summary.total_failed == 5
    assert summary.total_reported == 3
    assert summary.regressions_only is True
    assert isinstance(summary.regressions_only, bool)


def test_circuit_breaker_state_tripped_is_bool():
    state = CircuitBreakerState(attempt_count=3, max_attempts=3, tripped=True)
    assert state.tripped is True
    assert isinstance(state.tripped, bool)

    not_tripped = CircuitBreakerState(attempt_count=1, max_attempts=3, tripped=False)
    assert not_tripped.tripped is False


def test_ci_failure_payload_defaults():
    payload = CIFailurePayload()
    assert payload.event_type == "ci_failure"
    assert payload.sha == ""
    assert payload.branch == ""
    assert payload.pr_number is None
    assert payload.recommended_response == RecommendedResponse.SELF_CORRECT
    assert payload.actionable_failures == []
    assert payload.circuit_breaker.tripped is False
    assert payload.failure_summary.total_failed == 0


def test_ci_failure_payload_asdict():
    detail = FailureDetail(
        test_id="tests/test_foo.py::test_bar",
        category=FailureCategory.ASSERTION,
        was_passing_on_base=True,
        relevant_diff_hunks=[],
        failure_message="AssertionError",
    )
    payload = CIFailurePayload(
        sha="abc123",
        branch="feature/x",
        actionable_failures=[detail],
        recommended_response=RecommendedResponse.SELF_CORRECT,
    )
    d = dataclasses.asdict(payload)
    assert d["event_type"] == "ci_failure"
    assert d["recommended_response"] == "self_correct"
    assert d["actionable_failures"][0]["category"] == "assertion"
    assert d["circuit_breaker"]["tripped"] is False


def test_branch_context_payload_defaults():
    payload = BranchContextPayload()
    assert payload.event_type == "branch_context"
    assert payload.base_branch == "main"
    assert payload.ci_status == ""
    assert payload.open_pr_number is None
    assert payload.has_merge_conflict is False


def test_branch_context_payload_asdict():
    payload = BranchContextPayload(
        branch="main",
        head_sha="deadbeef",
        commits_ahead_of_base=2,
        ci_status="failure",
        open_pr_number=42,
        has_merge_conflict=True,
    )
    d = dataclasses.asdict(payload)
    assert d["event_type"] == "branch_context"
    assert d["ci_status"] == "failure"
    assert d["open_pr_number"] == 42
    assert d["has_merge_conflict"] is True


def test_review_comment_fields():
    comment = ReviewComment(
        comment_id=1,
        body="Please fix the naming.",
        author="reviewer1",
        author_type="human_review",
        file_path="src/db.py",
        line=42,
        is_blocking=True,
    )
    assert comment.comment_id == 1
    assert comment.author_type == "human_review"
    assert comment.is_blocking is True
    assert isinstance(comment.is_blocking, bool)


def test_review_comment_optional_defaults():
    comment = ReviewComment(
        comment_id=2,
        body="LGTM",
        author="bot[bot]",
        author_type="ci_automated",
    )
    assert comment.file_path is None
    assert comment.line is None
    assert comment.is_blocking is False


def test_review_comments_payload_defaults():
    payload = ReviewCommentsPayload()
    assert payload.event_type == "review_comments"
    assert payload.pr_number == 0
    assert payload.blocking_comments == []
    assert payload.total_comments == 0


def test_review_comments_payload_asdict():
    comment = ReviewComment(
        comment_id=10,
        body="Must fix.",
        author="alice",
        author_type="human_review",
        is_blocking=True,
    )
    payload = ReviewCommentsPayload(
        pr_number=7,
        blocking_comments=[comment],
        total_comments=3,
    )
    d = dataclasses.asdict(payload)
    assert d["event_type"] == "review_comments"
    assert d["pr_number"] == 7
    assert d["total_comments"] == 3
    assert d["blocking_comments"][0]["author_type"] == "human_review"
    assert d["blocking_comments"][0]["is_blocking"] is True
