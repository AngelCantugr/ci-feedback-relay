#!/bin/bash
# Quick CI gate — runs on subagentStop (subagent finishes a step).
# Skips pytest — the developer agent runs it internally before marking work done.
# Ruff + mypy are fast and catch the most common mistakes early in the loop.
# Always exits 0 — non-zero signals a hook infrastructure error, not a block.
set -uo pipefail

if ! command -v ruff &>/dev/null || ! command -v mypy &>/dev/null; then
  pip install -q -r requirements-dev.txt 2>&1
fi

set +e
ruff check . >/dev/null 2>&1; RUFF_EXIT=$?
ruff format --check . >/dev/null 2>&1; FMT_EXIT=$?
mypy src/ >/dev/null 2>&1; MYPY_EXIT=$?
set -e

if [ $RUFF_EXIT -ne 0 ] || [ $FMT_EXIT -ne 0 ] || [ $MYPY_EXIT -ne 0 ]; then
  full_output=$(
    echo "=== ruff check ===" && ruff check . 2>&1 || true
    echo "=== ruff format --check ===" && ruff format --check . 2>&1 || true
    echo "=== mypy src/ ===" && mypy src/ 2>&1 || true
  )
  jq -n --arg r "Quick CI check failed. Fix before finishing:

$full_output" '{"decision":"block","reason":$r}'
  exit 0
fi

echo '{"decision":"approve"}'
