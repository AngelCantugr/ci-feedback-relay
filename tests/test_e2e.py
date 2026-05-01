"""7-step E2E acceptance test: push failure → webhook → enrichment → channel → circuit breaker.

Steps:
  1  Push branch with deliberate failure → store push + check_run raw events
  2  Verify webhook stored both events   → raw_events has 'push' and 'check_run' rows
  3  Verify enrichment ran               → enriched_ci_failures row with self_correct
  4  Verify channel event queued         → channel queue has ci_failure event
  5  Query via MCP tool                  → get_ci_failure_context returns matching payload
  6  Second enrichment of same failure   → circuit_breaker.attempt_count = 2
  7  Third enrichment                    → tripped=True, recommended_response=escalate_to_human
"""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest

REPO = "angelcantugr/ci-feedback-relay"
E2E_SHA = "e2eshadeadbeef1234567890ab"
E2E_CHECK_RUN_ID = 77777
E2E_BRANCH = "test/e2e-failure"
E2E_FAILURE_TEXT = (
    "FAILED tests/test_e2e_deliberate.py::test_deliberate_failure"
    " - AssertionError: deliberate E2E test failure"
)


def _make_check_run_payload(
    sha: str,
    repo: str,
    check_run_id: int,
    branch: str,
    output_text: str = "",
) -> dict:
    return {
        "action": "completed",
        "check_run": {
            "id": check_run_id,
            "name": "CI / test (ubuntu-latest)",
            "head_sha": sha,
            "conclusion": "failure",
            "check_suite": {"head_branch": branch},
            "output": {
                "title": "Tests failed",
                "summary": "1 test failed",
                "text": output_text,
            },
            "pull_requests": [],
        },
        "repository": {"full_name": repo},
    }


@pytest.fixture()
def e2e_db(tmp_path, monkeypatch):
    """Isolated DB and mocked external services for the E2E test."""
    db_file = str(tmp_path / "e2e.db")
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", db_file)

    import src.db as db_mod

    importlib.reload(db_mod)
    db_mod.init_db()

    # Cause agent is unavailable in CI (no Ollama)
    monkeypatch.setattr("src.enricher.run_cause_agent", lambda *_: None)

    # Return the push event for the E2E sha so branch is resolved correctly
    def _fake_push(sha: str) -> dict | None:
        if sha == E2E_SHA:
            return {
                "ref": f"refs/heads/{E2E_BRANCH}",
                "after": sha,
                "before": "0000000000000000000000000000000000000000",
            }
        return None

    monkeypatch.setattr("src.enricher.get_push_for_sha", _fake_push)

    # GitHub client returns None → diff fetch raises AttributeError → caught by enricher
    monkeypatch.setattr("src.enricher.get_github_client", lambda: None)

    # Replace the shared channel queue with a fresh one for isolation
    import src.channel as channel_mod

    fresh_queue: asyncio.Queue = asyncio.Queue()
    monkeypatch.setattr(channel_mod, "_channel_queue", fresh_queue)

    return db_mod, fresh_queue


# ---------------------------------------------------------------------------
# Full 7-step E2E pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_7_step_pipeline(e2e_db):
    """Simulate the complete Layer-1 loop end-to-end without live external services."""
    db_mod, channel_queue = e2e_db
    cr_payload = _make_check_run_payload(
        E2E_SHA, REPO, E2E_CHECK_RUN_ID, E2E_BRANCH, E2E_FAILURE_TEXT
    )

    # -------------------------------------------------------------------
    # Step 1: Push a branch with a deliberate test failure
    #   Simulate the two webhook events that GitHub would fire:
    #   a push event followed by a check_run completed/failure event.
    # -------------------------------------------------------------------
    db_mod.store_raw_event(
        "push",
        "e2e-delivery-push-1",
        REPO,
        {
            "ref": f"refs/heads/{E2E_BRANCH}",
            "after": E2E_SHA,
            "before": "0000000000000000000000000000000000000000",
            "repository": {"full_name": REPO},
        },
    )
    db_mod.store_raw_event("check_run", "e2e-delivery-cr-1", REPO, cr_payload)

    # -------------------------------------------------------------------
    # Step 2: Verify webhook stored both events
    # -------------------------------------------------------------------
    with db_mod.get_conn() as conn:
        rows = conn.execute(
            "SELECT event_type FROM raw_events ORDER BY id ASC"
        ).fetchall()
    event_types = [r["event_type"] for r in rows]
    assert "push" in event_types, "push event must be stored in raw_events"
    assert "check_run" in event_types, "check_run event must be stored in raw_events"

    # -------------------------------------------------------------------
    # Step 3: Verify enrichment ran
    #   Pre-seed main branch with a different test so the E2E failure is
    #   a regression (was_passing_on_base=True) → recommended_response=self_correct.
    # -------------------------------------------------------------------
    db_mod.store_enriched_failure(
        "main-base-sha",
        "main",
        REPO,
        None,
        11111,
        "CI / test (ubuntu-latest)",
        {"actionable_failures": [{"test_id": "tests/test_other.py::test_baseline"}]},
    )

    from src.enricher import process_ci_failure

    await process_ci_failure(cr_payload)

    enriched = db_mod.get_enriched_failure(E2E_SHA)
    assert enriched is not None, "enriched_ci_failures must have a row for the sha"
    assert enriched["sha"] == E2E_SHA
    assert enriched["branch"] == E2E_BRANCH
    assert enriched["recommended_response"] == "self_correct", (
        f"first push must yield self_correct, got: {enriched['recommended_response']}"
    )
    assert enriched["circuit_breaker"]["attempt_count"] == 1
    assert enriched["circuit_breaker"]["tripped"] is False

    # -------------------------------------------------------------------
    # Step 4: Verify channel event arrives in Claude Code session
    # -------------------------------------------------------------------
    assert not channel_queue.empty(), "channel queue must have at least one event"
    channel_event = channel_queue.get_nowait()
    assert channel_event["source"] == "ci-feedback-relay"
    assert channel_event["event_type"] == "ci_failure"
    assert channel_event["recommended_response"] == "self_correct"
    assert "circuit_breaker" in channel_event
    assert channel_event["circuit_breaker"]["attempt_count"] == 1
    assert channel_event["circuit_breaker"]["tripped"] is False

    # -------------------------------------------------------------------
    # Step 5: Query via MCP tool get_ci_failure_context
    # -------------------------------------------------------------------
    from src.mcp_server import _get_ci_failure_context

    result = await _get_ci_failure_context(E2E_SHA)
    assert len(result) == 1
    mcp_data = json.loads(result[0].text)
    assert mcp_data["sha"] == E2E_SHA, (
        "MCP tool must return payload for the correct sha"
    )
    assert mcp_data["recommended_response"] == "self_correct"
    assert mcp_data["event_type"] == "ci_failure"

    # -------------------------------------------------------------------
    # Step 6: Push same failure again → attempt_count increments to 2
    #   Re-deliver the same check_run event (simulates a CI re-run or a
    #   duplicate webhook delivery for the same sha + check_run_id).
    # -------------------------------------------------------------------
    db_mod.store_raw_event("check_run", "e2e-delivery-cr-2", REPO, cr_payload)
    await process_ci_failure(cr_payload)

    enriched2 = db_mod.get_enriched_failure(E2E_SHA)
    assert enriched2 is not None
    assert enriched2["circuit_breaker"]["attempt_count"] == 2, (
        f"second attempt must set attempt_count=2, got {enriched2['circuit_breaker']['attempt_count']}"
    )
    assert enriched2["circuit_breaker"]["tripped"] is False

    channel_event2 = channel_queue.get_nowait()
    assert channel_event2["circuit_breaker"]["attempt_count"] == 2

    # -------------------------------------------------------------------
    # Step 7: Push a third time → circuit breaker trips
    # -------------------------------------------------------------------
    db_mod.store_raw_event("check_run", "e2e-delivery-cr-3", REPO, cr_payload)
    await process_ci_failure(cr_payload)

    enriched3 = db_mod.get_enriched_failure(E2E_SHA)
    assert enriched3 is not None
    assert enriched3["circuit_breaker"]["attempt_count"] == 3, (
        f"third attempt must set attempt_count=3, got {enriched3['circuit_breaker']['attempt_count']}"
    )
    assert enriched3["circuit_breaker"]["tripped"] is True, (
        "circuit breaker must be tripped after max_attempts (3)"
    )
    assert enriched3["recommended_response"] == "escalate_to_human", (
        f"tripped circuit breaker must yield escalate_to_human, got: {enriched3['recommended_response']}"
    )

    channel_event3 = channel_queue.get_nowait()
    assert channel_event3["circuit_breaker"]["tripped"] is True
    assert channel_event3["recommended_response"] == "escalate_to_human"
