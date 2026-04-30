"""Tests for src/enricher.py — CI failure enrichment pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payload(
    sha: str = "deadbeef",
    repo: str = "org/repo",
    check_run_id: int = 42,
    check_run_name: str = "CI / test",
    conclusion: str = "failure",
    pr_numbers: list[int] | None = None,
    output_text: str = "",
    head_branch: str = "feature/foo",
) -> dict:
    prs = [{"number": n} for n in (pr_numbers or [])]
    return {
        "action": "completed",
        "check_run": {
            "id": check_run_id,
            "name": check_run_name,
            "head_sha": sha,
            "conclusion": conclusion,
            "check_suite": {"head_branch": head_branch},
            "output": {"title": "Tests", "summary": "3 failed", "text": output_text},
            "pull_requests": prs,
        },
        "repository": {"full_name": repo},
    }


def _make_output_text(*test_ids: str) -> str:
    """Build synthetic pytest output text for given test IDs."""
    lines = []
    for tid in test_ids:
        lines.append(f"FAILED {tid} - AssertionError: expected True got False")
    return "\n".join(lines)


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


@pytest.fixture()
def mock_cause_agent_none(monkeypatch):
    """Patch run_cause_agent to always return None (Ollama unavailable)."""
    monkeypatch.setattr("src.enricher.run_cause_agent", lambda *_: None)


@pytest.fixture()
def mock_github_no_diff(monkeypatch):
    """Patch get_github_client so no diff is fetched (no base_sha available)."""
    monkeypatch.setattr("src.enricher.get_github_client", lambda: MagicMock())


@pytest.fixture()
def mock_push_none(monkeypatch):
    """Patch get_push_for_sha to return None (no push event stored)."""
    monkeypatch.setattr("src.enricher.get_push_for_sha", lambda sha: None)


# ---------------------------------------------------------------------------
# Acceptance criterion 1: process_ci_failure completes without error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_ci_failure_completes(db, mock_push_none, mock_cause_agent_none):
    """process_ci_failure must complete without raising for a valid payload."""
    from src.enricher import process_ci_failure

    payload = _make_payload(
        output_text=_make_output_text("tests/test_foo.py::test_bar")
    )
    await process_ci_failure(payload)  # must not raise


# ---------------------------------------------------------------------------
# Acceptance criterion 2: recommended_response=ESCALATE when circuit tripped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommended_escalate_when_tripped(
    db, mock_push_none, mock_cause_agent_none, monkeypatch
):
    """When circuit breaker trips, recommended_response must be ESCALATE."""
    monkeypatch.setattr("src.enricher.config.circuit_breaker.max_attempts", 1)

    from src.enricher import process_ci_failure

    payload = _make_payload(
        sha="tripped-sha", output_text=_make_output_text("tests/test_foo.py::test_bar")
    )
    await process_ci_failure(payload)

    result = db.get_enriched_failure("tripped-sha")
    assert result is not None
    assert result["recommended_response"] == "escalate_to_human"
    assert result["circuit_breaker"]["tripped"] is True


# ---------------------------------------------------------------------------
# Acceptance criterion 3: recommended_response=IGNORE when all not was_passing_on_base
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommended_ignore_when_no_regressions(
    db, mock_push_none, mock_cause_agent_none
):
    """When all failures were already failing on base, response should be IGNORE."""
    # Pre-seed main-branch failure with the same test_id so was_passing_on_base=False
    existing = {
        "actionable_failures": [{"test_id": "tests/test_foo.py::test_bar"}],
    }
    db.store_enriched_failure("old-sha", "main", "org/repo", None, 10, "CI", existing)

    from src.enricher import process_ci_failure

    payload = _make_payload(
        sha="new-sha",
        output_text=_make_output_text("tests/test_foo.py::test_bar"),
        head_branch="main",
    )
    await process_ci_failure(payload)

    result = db.get_enriched_failure("new-sha")
    assert result is not None
    assert result["recommended_response"] == "ignore"


# ---------------------------------------------------------------------------
# Acceptance criterion 4: recommended_response=RERUN when all FLAKE_SUSPECTED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommended_rerun_when_all_flake(
    db, mock_push_none, mock_cause_agent_none
):
    """When all failures categorize as FLAKE_SUSPECTED, response must be RERUN."""
    # No pre-seeded main failure → was_passing_on_base=False (no main record)
    # Use output that won't match any keyword → FLAKE_SUSPECTED
    flaky_text = "FAILED tests/test_foo.py::test_bar - some random network glitch"

    from src.enricher import process_ci_failure

    payload = _make_payload(sha="flake-sha", output_text=flaky_text)
    await process_ci_failure(payload)

    result = db.get_enriched_failure("flake-sha")
    assert result is not None
    assert result["recommended_response"] == "rerun"


# ---------------------------------------------------------------------------
# Acceptance criterion 5: recommended_response=SELF_CORRECT when regression present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommended_self_correct_when_regression(
    db, mock_push_none, mock_cause_agent_none
):
    """When at least one failure is a regression, response should be SELF_CORRECT."""
    # Pre-seed main branch with a DIFFERENT test → test_bar NOT in prev → was_passing=True
    existing = {
        "actionable_failures": [{"test_id": "tests/test_other.py::test_other"}],
    }
    db.store_enriched_failure("base-sha", "main", "org/repo", None, 11, "CI", existing)

    from src.enricher import process_ci_failure

    # Use AssertionError text so category is ASSERTION (not FLAKE_SUSPECTED)
    output = (
        "FAILED tests/test_foo.py::test_bar - AssertionError: expected True got False"
    )
    payload = _make_payload(sha="self-correct-sha", output_text=output)
    await process_ci_failure(payload)

    result = db.get_enriched_failure("self-correct-sha")
    assert result is not None
    assert result["recommended_response"] == "self_correct"


# ---------------------------------------------------------------------------
# Acceptance criterion 6: run_cause_agent returning None does not break enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cause_agent_none_does_not_break(db, mock_push_none, monkeypatch):
    """When run_cause_agent returns None, enrichment must still complete and persist."""
    monkeypatch.setattr("src.enricher.run_cause_agent", lambda *_: None)

    from src.enricher import process_ci_failure

    payload = _make_payload(
        sha="no-cause-sha",
        output_text=_make_output_text("tests/test_foo.py::test_bar"),
    )
    await process_ci_failure(payload)

    result = db.get_enriched_failure("no-cause-sha")
    assert result is not None
    # likely_cause is None in the stored payload for each failure
    for f in result.get("actionable_failures", []):
        assert f.get("likely_cause") is None


# ---------------------------------------------------------------------------
# Acceptance criterion 7: enriched payload stored in enriched_ci_failures table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enriched_payload_persisted_to_db(
    db, mock_push_none, mock_cause_agent_none
):
    """store_enriched_failure must write the full CIFailurePayload to the DB."""
    from src.enricher import process_ci_failure

    sha = "persist-sha"
    payload = _make_payload(
        sha=sha,
        check_run_id=999,
        check_run_name="CI / pytest",
        pr_numbers=[7],
        output_text=_make_output_text("tests/test_auth.py::test_login"),
    )
    await process_ci_failure(payload)

    result = db.get_enriched_failure(sha)
    assert result is not None
    assert result["sha"] == sha
    assert result["branch"] == "feature/foo"
    assert result["pr_number"] == 7
    assert result["failure_summary"]["check_run_name"] == "CI / pytest"
    assert result["failure_summary"]["total_failed"] == 1
    assert len(result["actionable_failures"]) == 1
    assert (
        result["actionable_failures"][0]["test_id"] == "tests/test_auth.py::test_login"
    )
    assert "circuit_breaker" in result
    assert "recommended_response" in result


# ---------------------------------------------------------------------------
# Additional: branch resolution from push event ref
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_branch_resolved_from_push_event(db, mock_cause_agent_none, monkeypatch):
    """Branch should be resolved from push event ref when available."""
    push_event = {
        "ref": "refs/heads/my-feature",
        "before": "base000",
        "after": "push-sha",
    }
    monkeypatch.setattr("src.enricher.get_push_for_sha", lambda sha: push_event)

    from src.enricher import process_ci_failure

    payload = _make_payload(sha="push-sha", head_branch="wrong-branch")
    await process_ci_failure(payload)

    result = db.get_enriched_failure("push-sha")
    assert result is not None
    assert result["branch"] == "my-feature"


# ---------------------------------------------------------------------------
# Additional: base_sha extracted from top-level "before" — diff fetch triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_fetched_when_before_present(db, mock_cause_agent_none, monkeypatch):
    """get_github_client must be called when push event has a top-level 'before' key."""
    push_event = {
        "ref": "refs/heads/main",
        "before": "base-sha-000",
        "after": "head-sha-111",
    }
    monkeypatch.setattr("src.enricher.get_push_for_sha", lambda sha: push_event)

    github_called = []

    def _mock_github():
        github_called.append(True)
        mock_repo = MagicMock()
        mock_repo.compare.return_value.files = []
        mock_client = MagicMock()
        mock_client.get_repo.return_value = mock_repo
        return mock_client

    monkeypatch.setattr("src.enricher.get_github_client", _mock_github)

    from src.enricher import process_ci_failure

    payload = _make_payload(sha="head-sha-111")
    await process_ci_failure(payload)

    assert github_called, "get_github_client must be called when base_sha is available"


# ---------------------------------------------------------------------------
# Additional: PR number extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_number_extracted(db, mock_push_none, mock_cause_agent_none):
    """PR number should be extracted from pull_requests list."""
    from src.enricher import process_ci_failure

    payload = _make_payload(sha="pr-sha", pr_numbers=[42])
    await process_ci_failure(payload)

    result = db.get_enriched_failure("pr-sha")
    assert result is not None
    assert result["pr_number"] == 42


@pytest.mark.asyncio
async def test_pr_number_none_when_empty(db, mock_push_none, mock_cause_agent_none):
    """PR number should be None when pull_requests list is empty."""
    from src.enricher import process_ci_failure

    payload = _make_payload(sha="no-pr-sha")
    await process_ci_failure(payload)

    result = db.get_enriched_failure("no-pr-sha")
    assert result is not None
    assert result["pr_number"] is None


# ---------------------------------------------------------------------------
# Additional: _parse_failures unit test
# ---------------------------------------------------------------------------


def test_parse_failures_extracts_test_ids():
    from src.enricher import _parse_failures

    text = (
        "FAILED tests/test_auth.py::test_login_valid - AssertionError\n"
        "FAILED tests/test_db.py::test_conn - TimeoutError\n"
        "some other output\n"
        "ERROR tests/test_foo.py::test_bar - ImportError\n"
    )
    failures = _parse_failures(text)
    test_ids = [f["test_id"] for f in failures]
    assert "tests/test_auth.py::test_login_valid" in test_ids
    assert "tests/test_db.py::test_conn" in test_ids
    assert "tests/test_foo.py::test_bar" in test_ids


# ---------------------------------------------------------------------------
# Additional: _categorize unit tests
# ---------------------------------------------------------------------------


def test_categorize_assertion():
    from src.enricher import _categorize
    from src.models import FailureCategory

    assert _categorize("AssertionError: expected 1 got 2") == FailureCategory.ASSERTION


def test_categorize_import_error():
    from src.enricher import _categorize
    from src.models import FailureCategory

    assert (
        _categorize("ModuleNotFoundError: No module named 'foo'")
        == FailureCategory.IMPORT_ERROR
    )


def test_categorize_timeout():
    from src.enricher import _categorize
    from src.models import FailureCategory

    assert _categorize("Timeout: test timed out after 30s") == FailureCategory.TIMEOUT


def test_categorize_infra():
    from src.enricher import _categorize
    from src.models import FailureCategory

    assert _categorize("ConnectionError: failed to connect") == FailureCategory.INFRA


def test_categorize_flake_suspected():
    from src.enricher import _categorize
    from src.models import FailureCategory

    assert (
        _categorize("random failure with no known pattern")
        == FailureCategory.FLAKE_SUSPECTED
    )


# ---------------------------------------------------------------------------
# Additional: _compute_recommended unit tests
# ---------------------------------------------------------------------------


def test_compute_recommended_empty_details_returns_ignore():
    from src.enricher import _compute_recommended
    from src.models import RecommendedResponse

    assert _compute_recommended([], tripped=False) == RecommendedResponse.IGNORE


def test_compute_recommended_tripped_overrides_all():
    from src.enricher import _compute_recommended
    from src.models import FailureCategory, FailureDetail, RecommendedResponse

    detail = FailureDetail(
        test_id="t",
        category=FailureCategory.FLAKE_SUSPECTED,
        was_passing_on_base=False,
        relevant_diff_hunks=[],
        failure_message="",
    )
    # Even with FLAKE_SUSPECTED, tripped=True → ESCALATE
    assert _compute_recommended([detail], tripped=True) == RecommendedResponse.ESCALATE


# ---------------------------------------------------------------------------
# Additional: _is_high_signal unit test
# ---------------------------------------------------------------------------


def test_is_high_signal_true_when_regression():
    from src.enricher import _is_high_signal
    from src.models import (
        CIFailurePayload,
        CircuitBreakerState,
        FailureCategory,
        FailureDetail,
        FailureSummary,
        RecommendedResponse,
    )

    failure = FailureDetail(
        test_id="t",
        category=FailureCategory.ASSERTION,
        was_passing_on_base=True,
        relevant_diff_hunks=[],
        failure_message="",
    )
    payload = CIFailurePayload(
        sha="abc",
        branch="main",
        failure_summary=FailureSummary(1, 1, "CI", "failure", True),
        actionable_failures=[failure],
        circuit_breaker=CircuitBreakerState(1, 3, False),
        recommended_response=RecommendedResponse.SELF_CORRECT,
    )
    assert _is_high_signal(payload) is True


def test_is_high_signal_false_when_no_regressions_and_not_tripped():
    from src.enricher import _is_high_signal
    from src.models import (
        CIFailurePayload,
        CircuitBreakerState,
        FailureCategory,
        FailureDetail,
        FailureSummary,
        RecommendedResponse,
    )

    failure = FailureDetail(
        test_id="t",
        category=FailureCategory.FLAKE_SUSPECTED,
        was_passing_on_base=False,
        relevant_diff_hunks=[],
        failure_message="",
    )
    payload = CIFailurePayload(
        sha="abc",
        branch="main",
        failure_summary=FailureSummary(1, 1, "CI", "failure", False),
        actionable_failures=[failure],
        circuit_breaker=CircuitBreakerState(1, 3, False),
        recommended_response=RecommendedResponse.IGNORE,
    )
    assert _is_high_signal(payload) is False
