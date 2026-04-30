#!/bin/bash
# Full CI gate — runs on agentStop (agent finishes a task).
# Checks: lint, format, types, tests.
# Outputs {"decision":"block","reason":"..."} on failure so the agent
# receives structured error context and can continue fixing.
# Always exits 0 — non-zero exit signals a hook infrastructure error.
set -uo pipefail

# Ensure dev deps are installed (ruff, mypy, pytest)
if ! command -v ruff &>/dev/null || ! command -v mypy &>/dev/null || ! command -v pytest &>/dev/null; then
  pip install -q -r requirements-dev.txt 2>&1
fi

output=$(
  ruff check . 2>&1 && \
  ruff format --check . 2>&1 && \
  mypy src/ 2>&1 && \
  pytest --tb=short -q 2>&1 || true
)

# Check exit status manually since we used || true above
set +e
ruff check . >/dev/null 2>&1; RUFF_EXIT=$?
ruff format --check . >/dev/null 2>&1; FMT_EXIT=$?
mypy src/ >/dev/null 2>&1; MYPY_EXIT=$?
pytest --tb=short -q >/dev/null 2>&1; PYTEST_EXIT=$?
set -e

if [ $RUFF_EXIT -ne 0 ] || [ $FMT_EXIT -ne 0 ] || [ $MYPY_EXIT -ne 0 ] || [ $PYTEST_EXIT -ne 0 ]; then
  # Re-run to capture full output for the reason field
  full_output=$(
    echo "=== ruff check ===" && ruff check . 2>&1 || true
    echo "=== ruff format --check ===" && ruff format --check . 2>&1 || true
    echo "=== mypy src/ ===" && mypy src/ 2>&1 || true
    echo "=== pytest ===" && pytest --tb=short -q 2>&1 || true
  )
  jq -n --arg r "CI gate failed. Fix all issues before finishing:

$full_output" '{"decision":"block","reason":$r}'
  exit 0
fi

echo '{"decision":"approve"}'
