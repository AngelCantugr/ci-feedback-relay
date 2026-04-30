"""Push and review event indexers: store structured data into SQLite from raw webhook payloads."""

from __future__ import annotations


async def process_push(payload: dict) -> None:
    """Index a push webhook payload into SQLite."""


async def process_review_comment(payload: dict) -> None:
    """Index a pull_request_review_comment webhook payload into SQLite."""


async def process_review(payload: dict) -> None:
    """Index a pull_request_review webhook payload into SQLite."""
