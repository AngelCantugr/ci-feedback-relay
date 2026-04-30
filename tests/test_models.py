"""Tests for src/models.py — core enums and dataclasses."""

from __future__ import annotations

import dataclasses

from src.models import (
    DiffHunk,
    FailureCategory,
    FailureDetail,
    LikelyCause,
    RecommendedResponse,
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
