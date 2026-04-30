# ci-feedback-relay — Agent & Developer Instructions

> Canonical reference for all AI agents (Claude Code, GitHub Copilot) and human developers.
> `CLAUDE.md` symlinks here — one source of truth.

---

## Project Overview

**ci-feedback-relay** is a GitHub App that closes the feedback loop for AI coding agents.
It intercepts CI failure webhook events, enriches them with diff context and a
LangGraph-generated root-cause hypothesis, and delivers structured payloads to a
Claude Code session via MCP tools and proactive Channel pushes.

**Hypothesis being tested**: Structured CI failure context injected into a Claude Code
session reduces CI runs per merged PR.

**Scope (Layer 1)**: Angel's personal repos only. Single Claude Code session. SQLite for
storage. Local Ollama for LLM enrichment. No cloud infra.

---

## Architecture

```
GitHub webhook events
        ↓
FastAPI receiver (src/webhook_handler.py)
  · HMAC signature verification
  · Raw event storage (raw_events table)
  · Event routing by X-GitHub-Event header
        ↓
Enrichment pipeline
  ├── src/indexer.py      — push + review event indexers (SQLite)
  ├── src/enricher.py     — diff join, failure categorization, regression detection
  └── src/cause_agent.py  — LangGraph ReAct agent → LikelyCause (Ollama)
        ↓
SQLite (data/events.db) — 4 tables
  · raw_events           — every webhook, idempotent via delivery_id
  · enriched_ci_failures — one row per enriched check_run failure
  · review_comments      — PR review comments with author_type annotation
  · circuit_breaker      — attempt tracking per (repo, sha, check_run_id)
        ↓
MCP Server (src/mcp_server.py) — stdio transport
  · get_ci_failure_context(sha)   — pull enriched CI failure payload
  · get_branch_context(branch)    — pull branch state + CI status
  · get_review_comments(pr_number)— pull blocking human review comments
  · register_branch_watch(branch) — internal, called by PostToolUse hook
  · claude/channel capability     — proactive push for high-signal events
        ↓
Claude Code session
  · .claude/mcp.json     — server discovery
  · .claude/settings.json — PostToolUse hook registration
  · .claude/hooks/post_tool_use.py — git push detection → register_branch_watch
```

### Module Dependency Order

Respect this import order. Never import in the reverse direction.

```
config.py → models.py → db.py → github_client.py
                                        ↓
                     indexer.py → cause_agent.py → enricher.py
                                                         ↓
                                               mcp_server.py
                                               webhook_handler.py
```

`config` and `models` have no internal dependencies. Everything else builds upward.
`webhook_handler` and `mcp_server` are the two top-level entry points — they depend on
everything below but nothing depends on them.

---

## Project Structure

```
ci-feedback-relay/
├── AGENTS.md                  ← you are here
├── CLAUDE.md                  ← symlink to AGENTS.md
├── config.yml                 ← non-secret settings (committed)
├── .env.example               ← env var template (committed, no values)
├── .env                       ← secrets (gitignored)
├── .keys/app.pem              ← GitHub App private key (gitignored)
├── requirements.txt           ← production dependencies
├── requirements-dev.txt       ← dev/test dependencies (ruff, mypy, pytest)
├── scripts/
│   ├── capture_baseline.py    ← one-shot: CI stats before MCP connection
│   └── start_dev.sh           ← start smee + uvicorn in parallel
├── src/
│   ├── __init__.py
│   ├── config.py              ← AppConfig typed loader (config.yml + .env overrides)
│   ├── models.py              ← all dataclasses: payloads, enums, supporting types
│   ├── db.py                  ← SQLite schema + CRUD helpers
│   ├── github_client.py       ← JWT → installation token (PyGitHub)
│   ├── indexer.py             ← push and review event indexers
│   ├── cause_agent.py         ← LangGraph ReAct agent for LikelyCause
│   ├── enricher.py            ← CI failure enrichment orchestration
│   └── mcp_server.py          ← MCP server: 3 pull tools + channel capability
├── data/
│   ├── baseline.json          ← CI stats before MCP (captured pre-connection)
│   └── events.db              ← SQLite database (gitignored)
├── .claude/
│   ├── mcp.json               ← MCP server discovery for Claude Code
│   ├── settings.json          ← PostToolUse hook registration
│   └── hooks/
│       └── post_tool_use.py   ← detect git push → register_branch_watch
└── .github/
    ├── AGENTS.md              ← this file (also at repo root)
    ├── copilot-instructions.md
    ├── agents/                ← Copilot custom agent definitions
    ├── hooks/                 ← CI gate hooks for Copilot workspace
    └── workflows/             ← GitHub Actions
```

---

## Issue Hierarchy

Issues use a three-level Objective → Phase → Task structure.

| Level | Label | Purpose |
|---|---|---|
| Objective | `objective` | Single top-level issue (#1) tracking the full Layer 1 build |
| Phase | `phase` | One per build step (0a through 5), each containing task checklists |
| Task | `task` | Leaf-level, AI-implementable, one file or concern per task |

**Build sequence**: 0a → 0b → 0c → 0d → 0e → 1 → 2 → 3 → 4 → 5

Each task issue includes: Context, Files, Implementation Notes (with code patterns from
spec), Acceptance Criteria, Dependencies. Implement the task, satisfy the criteria, close
the issue.

---

## Key Commands

```bash
# Development server (requires .env and Ollama running)
./scripts/start_dev.sh         # starts smee proxy + uvicorn together

# Run only the server
uvicorn src.webhook_handler:app --host 0.0.0.0 --port 8080 --reload

# Run only the MCP server
python -m src.mcp_server

# Baseline capture (run BEFORE connecting MCP — establishes "before" measurement)
GITHUB_TOKEN=... python scripts/capture_baseline.py \
  --repos angelcantugr/ci-feedback-relay --limit 20

# Tests
pytest                         # run all tests
pytest tests/test_enricher.py  # run single test file

# Linting and formatting
ruff check .                   # lint
ruff format .                  # format (in-place)
ruff format --check .          # format check (CI mode, no changes)

# Type checking
mypy src/

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

---

## Code Style & Conventions

### Python style
- Python 3.11+. Use `match` statements, `X | Y` union types, `list[X]` (not `List[X]`).
- All functions that touch I/O are `async`. Sync functions are pure computation only.
- `from __future__ import annotations` at the top of every file that uses forward references.
- Type every function signature. No untyped parameters, no implicit `Any`.

### Comments policy
- Never narrate what the code does. Comment only WHY (hidden constraint, non-obvious
  invariant, workaround for a specific external behavior).
- Public module-level docstrings: one line, describes the module's role in the pipeline.

### Dataclasses
- All data models are `@dataclass` (not Pydantic). `dataclasses.asdict()` is the
  serialization path to JSON.
- Use `field(default_factory=...)` for mutable defaults.
- Enum values are `str` enums so they serialize to strings without a custom JSON encoder.

### Database
- All SQL uses `CREATE TABLE IF NOT EXISTS` and `INSERT OR IGNORE` for idempotency.
- JSON fields stored as `json.dumps(...)` strings, returned as `json.loads(...)` dicts.
- Never let SQLite row dicts leak out of `db.py` — convert to typed structures before
  returning.

### Error handling
- External boundaries (GitHub API, Ollama, SQLite): catch, log, handle gracefully.
- Internal code: let exceptions propagate — don't swallow bugs.
- Hooks and background tasks: always `except Exception: pass` — never block the agent.

### Security
- HMAC signature verification on every webhook request before any processing.
- Secrets only from `.env` (never hardcoded, never in `config.yml`).
- Private key in `.keys/` (gitignored).
- No write permissions granted to the GitHub App — read-only throughout.

---

## Configuration

`config.yml` holds non-secret settings. `.env` holds secrets and overrides.

Required env vars (see `.env.example`):
- `GITHUB_APP_ID`
- `GITHUB_PRIVATE_KEY_PATH` (path to `.keys/app.pem`)
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_INSTALLATION_ID`
- `OLLAMA_BASE_URL` (optional, defaults to `http://localhost:11434`)
- `SMEE_URL` (smee.io channel for local development)

`from src.config import config` gives typed access to all settings everywhere.

---

## Payload Schema (src/models.py)

The three MCP pull tools return these top-level payloads:

| Tool | Payload type | Key fields |
|---|---|---|
| `get_ci_failure_context` | `CIFailurePayload` | `actionable_failures`, `recommended_response`, `circuit_breaker` |
| `get_branch_context` | `BranchContextPayload` | `ci_status`, `has_merge_conflict`, `commits_ahead_of_base` |
| `get_review_comments` | `ReviewCommentsPayload` | `blocking_comments` (only `is_blocking=True`) |

`recommended_response` values and their meanings:
- `self_correct` — regression present, agent should fix and repush
- `rerun` — all failures are `flake_suspected`, just re-trigger CI
- `escalate_to_human` — circuit breaker tripped (3 attempts), stop self-correcting
- `ignore` — all failures pre-existed on main, not caused by this branch

---

## Circuit Breaker

Tracked per `(repo, sha, check_run_id)` in the `circuit_breaker` table.

- Every channel push increments `attempt_count`.
- At `attempt_count >= max_attempts` (default 3): `tripped=True`.
- When tripped: `recommended_response=escalate_to_human`, no further channel pushes.
- Every payload includes current `CircuitBreakerState` so the agent always knows where it stands.

---

## Author Type Classification

Review comments are annotated with `author_type` to filter noise:

| Value | Meaning |
|---|---|
| `human_review` | Real person — act on this feedback |
| `ai_review` | CodeRabbit, Copilot, Sourcery — informational |
| `ci_automated` | Bot (`[bot]` suffix), Codecov, SonarCloud — ignore |
| `agent_self` | Claude Code agent's own comment — ignore |

The `get_review_comments` MCP tool returns only `is_blocking=True` comments so the agent
can focus on what actually blocks the merge.

---

## Custom Agents (GitHub Copilot)

Four agents are defined in `.github/agents/`:

| Agent | Role |
|---|---|
| `@dev-loop` | Orchestrator — coordinates explorer → developer → reviewer, up to 5 iterations until score ≥ 8/10 |
| `@explorer` | Read-only analyst — maps affected files, module dependencies, reuse candidates |
| `@developer` | Implementer — writes code following project conventions, validates with `ruff` + `mypy` + `pytest` |
| `@reviewer` | Outcome reviewer — scores 1–10 against the problem statement (not just acceptance criteria) |

Assign `@dev-loop` to any issue to start an autonomous development cycle.

---

## CI Gates (Copilot Workspace Hooks)

Two hooks defined in `.github/hooks/ci-gate.json`:

| Hook | Trigger | Checks |
|---|---|---|
| `ci-full` | `agentStop` (agent finishes) | `ruff check` + `ruff format --check` + `mypy src/` + `pytest` |
| `ci-quick` | `subagentStop` (subagent finishes) | `ruff check` + `ruff format --check` + `mypy src/` |

Both output `{"decision":"block","reason":"..."}` on failure so the agent receives
structured error context and can continue fixing rather than stopping.

---

## GitHub Actions

`.github/workflows/ci.yml` runs on every push/PR to `main`:

1. `ruff check .` — linting
2. `ruff format --check .` — formatting
3. `mypy src/` — type checking
4. `pytest` — tests (skipped gracefully if no test files exist yet)

Python 3.11, dependencies from `requirements.txt` + `requirements-dev.txt`.
