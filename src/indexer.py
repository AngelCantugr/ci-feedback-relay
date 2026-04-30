"""Push and review event indexers: store structured data into SQLite from raw webhook payloads."""

from __future__ import annotations

from src.db import store_review_comment

_KNOWN_AI: frozenset[str] = frozenset(
    {"coderabbitai", "github-copilot", "sourcery-ai", "deepsource-autofix"}
)
_KNOWN_CI: frozenset[str] = frozenset(
    {"codecov", "sonarcloud", "dependabot", "renovate", "github-actions"}
)


async def process_push(payload: dict) -> None:
    """Push event already stored in raw_events by the webhook handler.

    Raw events table is sufficient for Phase 3 sha lookup — no extra indexing needed.
    Keep as an extension point for Layer 2.
    """


async def process_review_comment(payload: dict) -> None:
    """Store inline review comment with author_type annotation."""
    comment = payload["comment"]
    pr_number = payload["pull_request"]["number"]
    repo = payload["repository"]["full_name"]
    author = comment["user"]["login"]
    author_type = _classify_author(author)

    store_review_comment(
        pr_number=pr_number,
        repo=repo,
        comment_id=comment["id"],
        author=author,
        author_type=author_type,
        body=comment["body"],
        file_path=comment.get("path"),
        line=comment.get("original_line"),
        is_blocking=False,
    )


async def process_review(payload: dict) -> None:
    """Store blocking review (changes_requested) as a comment with is_blocking=True."""
    review = payload["review"]
    pr_number = payload["pull_request"]["number"]
    repo = payload["repository"]["full_name"]
    author = review["user"]["login"]
    author_type = _classify_author(author)

    store_review_comment(
        pr_number=pr_number,
        repo=repo,
        comment_id=review["id"],
        author=author,
        author_type=author_type,
        body=review.get("body") or "",
        is_blocking=True,
    )


def _classify_author(login: str) -> str:
    """Classify a GitHub login into one of four author_type values.

    human_review   — real person: no [bot] suffix, not a known AI/CI app
    ai_review      — coderabbitai, github-copilot, sourcery-ai
    ci_automated   — [bot] suffix or known CI apps: codecov, sonarcloud, dependabot
    agent_self     — reserved for future comment-body signature detection
    """
    login_lower = login.lower()
    if login_lower.endswith("[bot]") or login_lower in _KNOWN_CI:
        return "ci_automated"
    if login_lower in _KNOWN_AI:
        return "ai_review"
    return "human_review"
