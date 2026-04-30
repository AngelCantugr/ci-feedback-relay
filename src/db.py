"""SQLite persistence layer: 4-table schema with indexes and CRUD helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

from src.config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,
    delivery_id     TEXT    UNIQUE,
    repo_full_name  TEXT    NOT NULL,
    payload         JSON    NOT NULL,
    received_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS enriched_ci_failures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sha             TEXT    NOT NULL,
    branch          TEXT    NOT NULL,
    repo_full_name  TEXT    NOT NULL,
    pr_number       INTEGER,
    check_run_id    INTEGER UNIQUE,
    check_run_name  TEXT,
    payload         JSON    NOT NULL,
    channel_pushed  INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_failures_sha    ON enriched_ci_failures(sha);
CREATE INDEX IF NOT EXISTS idx_failures_branch ON enriched_ci_failures(branch);

CREATE TABLE IF NOT EXISTS review_comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_number       INTEGER NOT NULL,
    repo_full_name  TEXT    NOT NULL,
    comment_id      INTEGER UNIQUE,
    author          TEXT,
    author_type     TEXT,
    body            TEXT,
    file_path       TEXT,
    line            INTEGER,
    is_blocking     INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_comments_pr ON review_comments(pr_number);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_full_name  TEXT    NOT NULL,
    sha             TEXT    NOT NULL,
    check_run_id    INTEGER NOT NULL,
    attempt_count   INTEGER DEFAULT 0,
    last_attempt_at DATETIME,
    tripped         INTEGER DEFAULT 0,
    UNIQUE(repo_full_name, sha, check_run_id)
);
"""


def init_db() -> None:
    """Execute all CREATE TABLE IF NOT EXISTS statements — safe to call multiple times."""
    db_path = config.db.path
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a sqlite3.Connection with row_factory = sqlite3.Row."""
    conn = sqlite3.connect(config.db.path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def store_raw_event(
    event_type: str, delivery_id: str, repo: str, payload: dict
) -> None:
    """INSERT OR IGNORE — idempotent via UNIQUE delivery_id."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_events
                (event_type, delivery_id, repo_full_name, payload)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, delivery_id, repo, json.dumps(payload)),
        )


def get_push_for_sha(sha: str) -> dict | None:
    """Return the push event payload for the given sha, or None."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT payload FROM raw_events
            WHERE event_type = 'push'
              AND json_extract(payload, '$.after') = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (sha,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload"])  # type: ignore[no-any-return]


def store_enriched_failure(
    sha: str,
    branch: str,
    repo: str,
    pr_number: int | None,
    check_run_id: int,
    check_run_name: str | None,
    payload: dict,
) -> None:
    """Upsert an enriched CI failure row."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO enriched_ci_failures
                (sha, branch, repo_full_name, pr_number, check_run_id, check_run_name, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(check_run_id) DO UPDATE SET
                sha            = excluded.sha,
                branch         = excluded.branch,
                repo_full_name = excluded.repo_full_name,
                pr_number      = excluded.pr_number,
                check_run_name = excluded.check_run_name,
                payload        = excluded.payload
            """,
            (
                sha,
                branch,
                repo,
                pr_number,
                check_run_id,
                check_run_name,
                json.dumps(payload),
            ),
        )


def get_enriched_failure(sha: str) -> dict | None:
    """Return the latest enriched payload JSON for the given sha, or None."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT payload FROM enriched_ci_failures
            WHERE sha = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (sha,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload"])  # type: ignore[no-any-return]


def store_review_comment(
    pr_number: int,
    repo: str,
    comment_id: int,
    author: str,
    author_type: str,
    body: str,
    file_path: str | None = None,
    line: int | None = None,
    is_blocking: bool = False,
) -> None:
    """INSERT OR IGNORE a review comment — idempotent via UNIQUE comment_id."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO review_comments
                (pr_number, repo_full_name, comment_id, author, author_type,
                 body, file_path, line, is_blocking)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pr_number,
                repo,
                comment_id,
                author,
                author_type,
                body,
                file_path,
                line,
                1 if is_blocking else 0,
            ),
        )


def get_review_comments(pr_number: int, repo: str) -> list[dict]:
    """Return all review comments for a PR as a list of dicts."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM review_comments
            WHERE pr_number = ? AND repo_full_name = ?
            ORDER BY id ASC
            """,
            (pr_number, repo),
        ).fetchall()
    return [dict(row) for row in rows]


def increment_circuit_breaker(
    repo: str, sha: str, check_run_id: int, max_attempts: int
) -> tuple[int, bool]:
    """Increment attempt_count for (repo, sha, check_run_id). Return (new_count, tripped)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO circuit_breaker (repo_full_name, sha, check_run_id, attempt_count, last_attempt_at)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(repo_full_name, sha, check_run_id) DO UPDATE SET
                attempt_count   = attempt_count + 1,
                last_attempt_at = CURRENT_TIMESTAMP
            """,
            (repo, sha, check_run_id),
        )
        row = conn.execute(
            """
            SELECT attempt_count FROM circuit_breaker
            WHERE repo_full_name = ? AND sha = ? AND check_run_id = ?
            """,
            (repo, sha, check_run_id),
        ).fetchone()
        new_count: int = row["attempt_count"]
        tripped = new_count >= max_attempts
        if tripped:
            conn.execute(
                """
                UPDATE circuit_breaker SET tripped = 1
                WHERE repo_full_name = ? AND sha = ? AND check_run_id = ?
                """,
                (repo, sha, check_run_id),
            )
    return (new_count, tripped)
