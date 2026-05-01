"""Tests for src/db.py — 4-table SQLite schema and CRUD helpers."""

from __future__ import annotations

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Patch config.db.path to a temp file and return a freshly initialised db module."""
    db_file = str(tmp_path / "test_events.db")

    # Patch before importing db so get_conn() uses the temp path
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", db_file)

    import importlib

    import src.db as db_mod

    importlib.reload(db_mod)
    db_mod.init_db()
    return db_mod


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_is_idempotent(db):
    """Calling init_db() a second time must not raise."""
    db.init_db()
    db.init_db()


# ---------------------------------------------------------------------------
# store_raw_event / get_push_for_sha
# ---------------------------------------------------------------------------


def test_store_raw_event_basic(db):
    db.store_raw_event("push", "delivery-1", "org/repo", {"after": "abc123"})
    result = db.get_push_for_sha("abc123")
    assert result == {"after": "abc123"}


def test_store_raw_event_duplicate_delivery_id_does_not_raise(db):
    """Duplicate delivery_id must be silently ignored (INSERT OR IGNORE)."""
    db.store_raw_event("push", "delivery-dup", "org/repo", {"after": "sha1"})
    # Second call with the same delivery_id — must not raise
    db.store_raw_event("push", "delivery-dup", "org/repo", {"after": "sha1"})


def test_get_push_for_sha_returns_none_when_absent(db):
    assert db.get_push_for_sha("nonexistent-sha") is None


def test_get_push_for_sha_matches_only_push_events(db):
    db.store_raw_event("check_run", "delivery-cr", "org/repo", {"after": "sha-cr"})
    # check_run event should NOT be returned by get_push_for_sha
    assert db.get_push_for_sha("sha-cr") is None


# ---------------------------------------------------------------------------
# store_enriched_failure / get_enriched_failure
# ---------------------------------------------------------------------------


def test_store_and_get_enriched_failure(db):
    payload = {"test_id": "tests/test_foo.py::test_bar", "category": "assertion"}
    db.store_enriched_failure("sha1", "main", "org/repo", None, 42, "CI", payload)
    result = db.get_enriched_failure("sha1")
    assert result == payload


def test_get_enriched_failure_returns_none_when_absent(db):
    assert db.get_enriched_failure("unknown-sha") is None


def test_store_enriched_failure_upsert_on_duplicate_check_run_id(db):
    """Storing a second row for the same check_run_id updates the existing row."""
    db.store_enriched_failure("sha1", "main", "org/repo", None, 99, "CI", {"v": 1})
    db.store_enriched_failure("sha1", "main", "org/repo", None, 99, "CI", {"v": 2})
    result = db.get_enriched_failure("sha1")
    assert result == {"v": 2}


def test_get_enriched_failure_returns_latest_for_sha(db):
    db.store_enriched_failure("sha2", "feat", "org/repo", None, 101, "CI", {"v": 1})
    db.store_enriched_failure("sha2", "feat", "org/repo", None, 102, "CI", {"v": 2})
    result = db.get_enriched_failure("sha2")
    assert result == {"v": 2}


# ---------------------------------------------------------------------------
# store_review_comment / get_review_comments
# ---------------------------------------------------------------------------


def test_store_and_get_review_comments(db):
    db.store_review_comment(
        7, "org/repo", 1001, "alice", "human_review", "Fix this.", is_blocking=True
    )
    db.store_review_comment(7, "org/repo", 1002, "bot[bot]", "ci_automated", "LGTM")
    comments = db.get_review_comments(7, "org/repo")
    assert len(comments) == 2
    blocking = [c for c in comments if c["is_blocking"] == 1]
    assert len(blocking) == 1
    assert blocking[0]["author"] == "alice"


def test_get_review_comments_scoped_to_repo(db):
    db.store_review_comment(1, "org/repo-a", 2001, "alice", "human_review", "ok")
    db.store_review_comment(1, "org/repo-b", 2002, "bob", "human_review", "nope")
    assert len(db.get_review_comments(1, "org/repo-a")) == 1
    assert len(db.get_review_comments(1, "org/repo-b")) == 1


def test_get_review_comments_empty_list_when_none(db):
    assert db.get_review_comments(999, "org/repo") == []


# ---------------------------------------------------------------------------
# increment_circuit_breaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_increments(db):
    count, tripped = db.increment_circuit_breaker(
        "org/repo", "sha1", 10, max_attempts=3
    )
    assert count == 1
    assert tripped is False


def test_circuit_breaker_trips_at_max(db):
    db.increment_circuit_breaker("org/repo", "sha1", 10, max_attempts=3)
    db.increment_circuit_breaker("org/repo", "sha1", 10, max_attempts=3)
    count, tripped = db.increment_circuit_breaker(
        "org/repo", "sha1", 10, max_attempts=3
    )
    assert count == 3
    assert tripped is True


def test_circuit_breaker_separate_keys_independent(db):
    db.increment_circuit_breaker("org/repo", "sha-a", 10, max_attempts=3)
    db.increment_circuit_breaker("org/repo", "sha-a", 10, max_attempts=3)
    db.increment_circuit_breaker("org/repo", "sha-a", 10, max_attempts=3)
    # Different sha — should not be tripped
    count, tripped = db.increment_circuit_breaker(
        "org/repo", "sha-b", 10, max_attempts=3
    )
    assert count == 1
    assert tripped is False


def test_circuit_breaker_tripped_flag_persists(db):
    """After tripping, the tripped column stays 1 in the database."""
    for _ in range(3):
        db.increment_circuit_breaker("org/repo", "sha1", 20, max_attempts=3)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT tripped FROM circuit_breaker WHERE sha = 'sha1' AND check_run_id = 20"
        ).fetchone()
    assert row["tripped"] == 1


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_json_roundtrip_raw_event(db):
    """Payload stored as JSON string and returned as dict."""
    data = {"after": "abc", "nested": {"key": [1, 2, 3]}}
    db.store_raw_event("push", "d-json", "org/repo", data)
    result = db.get_push_for_sha("abc")
    assert result == data
    assert isinstance(result, dict)


def test_json_roundtrip_enriched_failure(db):
    data = {"failures": [{"test_id": "t", "category": "assertion"}]}
    db.store_enriched_failure("sha-json", "main", "org/repo", 1, 55, "CI", data)
    result = db.get_enriched_failure("sha-json")
    assert result == data
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# store_branch_watch
# ---------------------------------------------------------------------------


def test_store_branch_watch_basic(db):
    """store_branch_watch persists branch and session_id and can be retrieved."""
    db.store_branch_watch("feature/test", "session-abc")
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT branch, session_id FROM watched_branches WHERE branch = ?",
            ("feature/test",),
        ).fetchone()
    assert row is not None
    assert row["branch"] == "feature/test"
    assert row["session_id"] == "session-abc"


def test_store_branch_watch_idempotent(db):
    """Calling store_branch_watch twice with the same branch updates session_id (INSERT OR REPLACE)."""
    db.store_branch_watch("main", "session-1")
    db.store_branch_watch("main", "session-2")
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT session_id FROM watched_branches WHERE branch = 'main'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "session-2"
