"""Tests for src/indexer.py — push and review event indexers."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Patch config.db.path to a temp file, init schema, and return db module."""
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", str(tmp_path / "test_events.db"))

    import src.db as db_mod

    importlib.reload(db_mod)
    db_mod.init_db()
    return db_mod


# ---------------------------------------------------------------------------
# process_push — no-op extension point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_push_is_noop(db):
    """process_push must not raise and must not write to the database."""
    import src.indexer as idx

    importlib.reload(idx)

    await idx.process_push(
        {
            "repository": {"full_name": "org/repo"},
            "after": "abc123",
            "ref": "refs/heads/main",
        }
    )

    # No rows should have been added to review_comments
    assert db.get_review_comments(1, "org/repo") == []


# ---------------------------------------------------------------------------
# process_review_comment
# ---------------------------------------------------------------------------

_REVIEW_COMMENT_PAYLOAD = {
    "action": "created",
    "pull_request": {"number": 42},
    "repository": {"full_name": "org/repo"},
    "comment": {
        "id": 1001,
        "user": {"login": "alice"},
        "body": "Please fix this.",
        "path": "src/main.py",
        "original_line": 10,
    },
}


@pytest.mark.asyncio
async def test_process_review_comment_stores_row(db):
    import src.indexer as idx

    importlib.reload(idx)

    await idx.process_review_comment(_REVIEW_COMMENT_PAYLOAD)

    comments = db.get_review_comments(42, "org/repo")
    assert len(comments) == 1
    c = comments[0]
    assert c["comment_id"] == 1001
    assert c["author"] == "alice"
    assert c["author_type"] == "human_review"
    assert c["body"] == "Please fix this."
    assert c["file_path"] == "src/main.py"
    assert c["line"] == 10
    assert c["is_blocking"] == 0


@pytest.mark.asyncio
async def test_process_review_comment_duplicate_does_not_raise(db):
    import src.indexer as idx

    importlib.reload(idx)

    await idx.process_review_comment(_REVIEW_COMMENT_PAYLOAD)
    await idx.process_review_comment(_REVIEW_COMMENT_PAYLOAD)  # same comment_id

    assert len(db.get_review_comments(42, "org/repo")) == 1


# ---------------------------------------------------------------------------
# process_review
# ---------------------------------------------------------------------------

_REVIEW_PAYLOAD = {
    "action": "submitted",
    "pull_request": {"number": 7},
    "repository": {"full_name": "org/repo"},
    "review": {
        "id": 9001,
        "user": {"login": "bob"},
        "state": "changes_requested",
        "body": "Needs work.",
    },
}


@pytest.mark.asyncio
async def test_process_review_stores_blocking_row(db):
    import src.indexer as idx

    importlib.reload(idx)

    await idx.process_review(_REVIEW_PAYLOAD)

    comments = db.get_review_comments(7, "org/repo")
    assert len(comments) == 1
    c = comments[0]
    assert c["comment_id"] == 9001
    assert c["author"] == "bob"
    assert c["author_type"] == "human_review"
    assert c["is_blocking"] == 1


@pytest.mark.asyncio
async def test_process_review_duplicate_does_not_raise(db):
    import src.indexer as idx

    importlib.reload(idx)

    await idx.process_review(_REVIEW_PAYLOAD)
    await idx.process_review(_REVIEW_PAYLOAD)

    assert len(db.get_review_comments(7, "org/repo")) == 1


@pytest.mark.asyncio
async def test_process_review_null_body_stored_as_empty_string(db):
    import src.indexer as idx

    importlib.reload(idx)

    payload = {
        "action": "submitted",
        "pull_request": {"number": 7},
        "repository": {"full_name": "org/repo"},
        "review": {
            "id": 9002,
            "user": {"login": "carol"},
            "state": "changes_requested",
            "body": None,
        },
    }
    await idx.process_review(payload)

    comments = db.get_review_comments(7, "org/repo")
    assert comments[0]["body"] == ""


# ---------------------------------------------------------------------------
# _classify_author
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        ("alice", "human_review"),
        ("BobSmith", "human_review"),
        ("coderabbitai", "ai_review"),
        ("CODERABBITAI", "ai_review"),
        ("github-copilot", "ai_review"),
        ("sourcery-ai", "ai_review"),
        ("deepsource-autofix", "ai_review"),
        ("dependabot[bot]", "ci_automated"),
        ("some-random-bot[bot]", "ci_automated"),
        ("codecov", "ci_automated"),
        ("sonarcloud", "ci_automated"),
        ("dependabot", "ci_automated"),
        ("renovate", "ci_automated"),
        ("github-actions", "ci_automated"),
    ],
)
def test_classify_author(login, expected):
    from src.indexer import _classify_author

    assert _classify_author(login) == expected
