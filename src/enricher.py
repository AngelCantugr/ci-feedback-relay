"""CI failure enrichment orchestration: diff join, categorization, and cause analysis."""

from __future__ import annotations


async def process_ci_failure(payload: dict) -> None:
    """Enrich a check_run failure payload and persist the result."""
