#!/usr/bin/env bash
set -euo pipefail

# Load .env
if [ -f .env ]; then
  set -a
  # shellcheck source=.env
  source .env
  set +a
fi

SMEE_URL="${SMEE_URL:-}"
if [ -z "$SMEE_URL" ]; then
  echo "ERROR: SMEE_URL not set in .env" >&2
  exit 1
fi

# Start smee proxy in background
smee --url "$SMEE_URL" --target http://localhost:8080/webhook &
SMEE_PID=$!

# Start uvicorn
uvicorn src.webhook_handler:app --host 0.0.0.0 --port 8080 --reload &
UVICORN_PID=$!

# Trap Ctrl-C and kill both process groups
trap "kill -- -$SMEE_PID -$UVICORN_PID 2>/dev/null; wait; exit" INT TERM

wait
