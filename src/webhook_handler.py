"""FastAPI webhook receiver: HMAC verification, raw event storage, and async event routing."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from src.config import config
from src.db import init_db, store_raw_event
from src.enricher import process_ci_failure
from src.indexer import process_push, process_review, process_review_comment


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


def _verify_signature(body: bytes, sig_header: str | None, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header or "")


@app.post("/webhook")
async def handle_webhook(
    request: Request,
    x_github_event: str = Header(...),
    x_github_delivery: str = Header(...),
    x_hub_signature_256: str | None = Header(None),
) -> dict:
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256, config.github.webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    repo_full_name = payload.get("repository", {}).get("full_name", "unknown")

    store_raw_event(x_github_event, x_github_delivery, repo_full_name, payload)

    match x_github_event:
        case "check_run":
            if (
                payload["action"] == "completed"
                and payload["check_run"]["conclusion"] == "failure"
            ):
                asyncio.create_task(process_ci_failure(payload))
        case "push":
            asyncio.create_task(process_push(payload))
        case "pull_request_review_comment":
            if payload["action"] == "created":
                asyncio.create_task(process_review_comment(payload))
        case "pull_request_review":
            if (
                payload["action"] == "submitted"
                and payload["review"]["state"] == "changes_requested"
            ):
                asyncio.create_task(process_review(payload))

    return {"ok": True}


@app.post("/internal/register-watch")
async def register_watch(request: Request) -> dict[str, bool]:
    body = await request.json()
    branch = body.get("branch", "")
    if not branch or not isinstance(branch, str):
        raise HTTPException(status_code=422, detail="Missing required field: branch")
    from src.db import store_branch_watch

    store_branch_watch(branch, body.get("session_id", "default"))
    return {"ok": True}
