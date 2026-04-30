# ci-feedback-relay — Copilot Instructions

A GitHub App that intercepts CI failures and enriches them with LangGraph/Ollama root-cause
analysis, then delivers structured payloads to a Claude Code session via MCP.

Full project context, architecture, conventions, and agent instructions are in
[AGENTS.md](../AGENTS.md).

## Quick Reference

|                |                                                                  |
| -------------- | ---------------------------------------------------------------- |
| **Stack**      | Python 3.11+, FastAPI, SQLite (stdlib), LangGraph, Ollama        |
| **Auth**       | GitHub App — JWT → installation access token (PyGitHub)          |
| **MCP**        | stdio transport, 3 pull tools + `claude/channel` capability      |
| **Lint/Format**| `ruff check .` / `ruff format --check .`                        |
| **Types**      | `mypy src/`                                                      |
| **Test**       | `pytest`                                                         |
| **Dev server** | `./scripts/start_dev.sh` (smee proxy + uvicorn)                  |

## Module Dependency Order

```
config → models → db → github_client → indexer → cause_agent → enricher/mcp_server → webhook_handler
```

Never import in reverse. `config` and `models` have no internal imports.

## Custom Agents

- **`@dev-loop`** — Orchestrates the full dev workflow: explores → implements → reviews,
  up to 5 iterations until score ≥ 8/10
- **`@explorer`** — Read-only codebase audit; maps affected files, module dependencies,
  and reuse candidates before any implementation starts
- **`@developer`** — Senior Python engineer; implements features/fixes and validates with
  `ruff check` + `mypy src/` + `pytest`
- **`@reviewer`** — Outcome-focused reviewer; scores implementation 1–10 against the
  original problem statement (not just acceptance criteria)

Assign `@dev-loop` to any issue to start an autonomous dev cycle.

## Issue Structure

Issues #1–#33 form the complete Layer 1 build plan: Objective (#1) → Phase (#2–#11) →
Task (#12–#33). Each task has implementation notes, code patterns, and acceptance criteria.
Start from the lowest-numbered open task with no unresolved dependencies.
