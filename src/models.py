"""Shared type contract: enums and per-failure dataclasses used across all pipeline modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureCategory(str, Enum):
    ASSERTION = "assertion"
    IMPORT_ERROR = "import_error"
    TIMEOUT = "timeout"
    INFRA = "infra"
    FLAKE_SUSPECTED = "flake_suspected"


class RecommendedResponse(str, Enum):
    SELF_CORRECT = "self_correct"
    RERUN = "rerun"
    ESCALATE = "escalate_to_human"
    IGNORE = "ignore"


@dataclass
class DiffHunk:
    file_path: str
    hunk_header: str  # @@ -L,S +L,S @@
    content: str
    change_type: str  # added | removed | modified


@dataclass
class LikelyCause:
    hypothesis: str  # e.g. "assertUserExists renamed in utils.py but test not updated"
    reasoning_steps: int  # ReAct iterations that ran
    model: str  # e.g. "deepseek-coder-v2"


@dataclass
class FailureDetail:
    test_id: str  # e.g. "tests/test_auth.py::test_login_valid"
    category: FailureCategory
    was_passing_on_base: bool  # True = this is a regression
    relevant_diff_hunks: list[DiffHunk]
    failure_message: str  # truncated raw output
    test_body: str | None = None  # test function source, configurable max_lines
    likely_cause: LikelyCause | None = None  # None if Ollama unavailable/timed out
