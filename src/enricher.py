"""CI failure enrichment orchestration: diff join, categorization, and cause analysis."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from src.cause_agent import DiffContext, run_cause_agent
from src.config import config
from src.db import (
    get_conn,
    get_push_for_sha,
    increment_circuit_breaker,
    store_enriched_failure,
)
from src.github_client import get_github_client
from src.models import (
    CIFailurePayload,
    CircuitBreakerState,
    DiffHunk,
    FailureCategory,
    FailureDetail,
    FailureSummary,
    RecommendedResponse,
)


async def process_ci_failure(payload: dict) -> None:
    """Enrich a check_run failure payload and persist the result."""
    check_run = payload["check_run"]
    sha = check_run["head_sha"]
    branch = check_run["check_suite"]["head_branch"]
    repo_name = payload["repository"]["full_name"]
    check_run_id = check_run["id"]
    check_run_name = check_run["name"]

    # Try to get better branch from push event
    push = get_push_for_sha(sha)
    if push:
        ref = push.get("ref", "")
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/") :]

    # PR number
    prs = check_run.get("pull_requests", [])
    pr_number = prs[0]["number"] if prs else None

    # Base SHA for diff — push IS the decoded webhook body; "before" is top-level
    base_sha = push.get("before") if push else None

    # Fetch diff
    diff_hunks: list[DiffHunk] = []
    if base_sha:
        try:
            g = get_github_client()
            repo = g.get_repo(repo_name)
            diff_hunks = _extract_diff_hunks(repo, base_sha, sha)
        except Exception:
            pass

    diff_context = DiffContext(
        hunks=diff_hunks,
        full_diff="\n".join(h.content for h in diff_hunks),
    )

    # Parse failures from check_run output
    output_text = (check_run.get("output") or {}).get("text") or ""
    raw_failures = _parse_failures(output_text)

    # Build FailureDetail list
    details: list[FailureDetail] = []
    for tf in raw_failures[: config.enrichment.max_actionable_failures]:
        category = _categorize(tf["message"])
        was_passing_on_base = _was_passing_on_base(tf["test_id"], repo_name)
        relevant_hunks = _find_relevant_hunks(diff_hunks, tf)
        fd = FailureDetail(
            test_id=tf["test_id"],
            category=category,
            was_passing_on_base=was_passing_on_base,
            relevant_diff_hunks=relevant_hunks,
            failure_message=tf["message"][:500],
        )
        # Only run the cause agent for regressions to avoid unnecessary LLM calls
        fd.likely_cause = (
            run_cause_agent(fd, diff_context) if was_passing_on_base else None
        )
        details.append(fd)

    # Circuit breaker
    attempt_count, tripped = increment_circuit_breaker(
        repo_name, sha, check_run_id, config.circuit_breaker.max_attempts
    )
    cb_state = CircuitBreakerState(
        attempt_count=attempt_count,
        max_attempts=config.circuit_breaker.max_attempts,
        tripped=tripped,
    )

    recommended = _compute_recommended(details, tripped)

    failure_payload = CIFailurePayload(
        sha=sha,
        branch=branch,
        pr_number=pr_number,
        failure_summary=FailureSummary(
            total_failed=len(raw_failures),
            total_reported=len(details),
            check_run_name=check_run_name,
            conclusion=check_run.get("conclusion", "failure"),
            regressions_only=all(d.was_passing_on_base for d in details)
            if details
            else False,
        ),
        actionable_failures=details,
        circuit_breaker=cb_state,
        recommended_response=recommended,
    )

    store_enriched_failure(
        sha,
        branch,
        repo_name,
        pr_number,
        check_run_id,
        check_run_name,
        asdict(failure_payload),
    )

    if _is_high_signal(failure_payload):
        await _push_to_channel(failure_payload)


def _parse_failures(text: str) -> list[dict]:
    failures = []
    for match in re.finditer(r"(FAILED|ERROR)\s+([\w/\.]+::[\w]+)", text):
        test_id = match.group(2)
        start = match.start()
        failures.append({"test_id": test_id, "message": text[start : start + 300]})
    return failures


def _categorize(message: str) -> FailureCategory:
    msg = message.lower()
    if "assertionerror" in msg or "assert " in msg:
        return FailureCategory.ASSERTION
    if "modulenotfounderror" in msg or "importerror" in msg:
        return FailureCategory.IMPORT_ERROR
    if "timeout" in msg or "timed out" in msg:
        return FailureCategory.TIMEOUT
    if "connectionerror" in msg or "dockererror" in msg:
        return FailureCategory.INFRA
    return FailureCategory.FLAKE_SUSPECTED


def _was_passing_on_base(test_id: str, repo: str) -> bool:
    # Conservative baseline: no main-branch record means insufficient data → not a regression
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload FROM enriched_ci_failures WHERE branch=? AND repo_full_name=? ORDER BY created_at DESC LIMIT 1",
            ("main", repo),
        ).fetchone()
    if not row:
        return False
    prev = (
        json.loads(row["payload"])
        if isinstance(row["payload"], str)
        else row["payload"]
    )
    prev_test_ids = {f["test_id"] for f in prev.get("actionable_failures", [])}
    return test_id not in prev_test_ids


def _extract_diff_hunks(repo: object, base_sha: str, head_sha: str) -> list[DiffHunk]:
    comparison = repo.compare(base_sha, head_sha)  # type: ignore[attr-defined]
    hunks = []
    for f in comparison.files:
        if not f.patch:
            continue
        hunks.append(
            DiffHunk(
                file_path=f.filename,
                hunk_header=f.patch.split("\n")[0],
                content=f.patch,
                change_type=f.status,
            )
        )
    return hunks


def _find_relevant_hunks(hunks: list[DiffHunk], failure: dict) -> list[DiffHunk]:
    return [h for h in hunks if h.file_path in failure.get("message", "")]


def _compute_recommended(
    details: list[FailureDetail], tripped: bool
) -> RecommendedResponse:
    if tripped:
        return RecommendedResponse.ESCALATE
    if not details:
        return RecommendedResponse.IGNORE
    if all(d.category == FailureCategory.FLAKE_SUSPECTED for d in details):
        return RecommendedResponse.RERUN
    if all(not d.was_passing_on_base for d in details):
        return RecommendedResponse.IGNORE
    return RecommendedResponse.SELF_CORRECT


def _is_high_signal(payload: CIFailurePayload) -> bool:
    return (
        any(d.was_passing_on_base for d in payload.actionable_failures)
        or payload.circuit_breaker.tripped
    )


async def _push_to_channel(payload: CIFailurePayload) -> None:
    """Push to Claude Code channel. Stub — implemented in Phase 5 (#30)."""
    pass
