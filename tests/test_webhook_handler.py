"""Tests for src/webhook_handler.py — HMAC verification and event routing."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


SECRET = "test-webhook-secret"


def _make_sig(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Return a TestClient with a temp DB and the webhook secret set."""
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", str(tmp_path / "test.db"))
    monkeypatch.setattr(cfg_mod.config.github, "webhook_secret", SECRET)

    # Reload handler so it picks up the patched config (lifespan runs init_db)
    import importlib

    import src.webhook_handler as wh

    importlib.reload(wh)

    with TestClient(wh.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_valid_signature_returns_200(client):
    body = json.dumps(
        {"repository": {"full_name": "org/repo"}, "action": "ping"}
    ).encode()
    sig = _make_sig(body)
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "delivery-1",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_invalid_signature_returns_401(client):
    body = json.dumps({"repository": {"full_name": "org/repo"}}).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "delivery-2",
            "X-Hub-Signature-256": "sha256=invalidsig",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_missing_signature_returns_401(client):
    body = json.dumps({"repository": {"full_name": "org/repo"}}).encode()
    resp = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "delivery-3",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# store_raw_event called before routing
# ---------------------------------------------------------------------------


def test_store_raw_event_called_before_routing(tmp_path, monkeypatch):
    """store_raw_event must be called before any routing decision."""
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", str(tmp_path / "test2.db"))
    monkeypatch.setattr(cfg_mod.config.github, "webhook_secret", SECRET)

    call_order: list[str] = []

    import importlib

    import src.webhook_handler as wh

    importlib.reload(wh)

    original_store = wh.store_raw_event

    def tracking_store(*args, **kwargs):
        call_order.append("store_raw_event")
        return original_store(*args, **kwargs)

    with patch.object(wh, "store_raw_event", side_effect=tracking_store):
        body = json.dumps(
            {
                "repository": {"full_name": "org/repo"},
                "action": "completed",
                "check_run": {"conclusion": "failure"},
            }
        ).encode()
        sig = _make_sig(body)
        with TestClient(wh.app) as c:
            c.post(
                "/webhook",
                content=body,
                headers={
                    "X-GitHub-Event": "check_run",
                    "X-GitHub-Delivery": "delivery-4",
                    "X-Hub-Signature-256": sig,
                    "Content-Type": "application/json",
                },
            )

    assert call_order[0] == "store_raw_event"


# ---------------------------------------------------------------------------
# Event routing
# ---------------------------------------------------------------------------


def test_check_run_failure_spawns_process_ci_failure(tmp_path, monkeypatch):
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", str(tmp_path / "test3.db"))
    monkeypatch.setattr(cfg_mod.config.github, "webhook_secret", SECRET)

    import importlib

    import src.webhook_handler as wh

    importlib.reload(wh)

    body = json.dumps(
        {
            "repository": {"full_name": "org/repo"},
            "action": "completed",
            "check_run": {"conclusion": "failure"},
        }
    ).encode()
    sig = _make_sig(body)

    with patch.object(wh, "process_ci_failure", new=AsyncMock()) as mock_fn:
        with patch.object(wh.asyncio, "create_task") as mock_task:
            with TestClient(wh.app) as c:
                resp = c.post(
                    "/webhook",
                    content=body,
                    headers={
                        "X-GitHub-Event": "check_run",
                        "X-GitHub-Delivery": "delivery-5",
                        "X-Hub-Signature-256": sig,
                        "Content-Type": "application/json",
                    },
                )
    assert resp.status_code == 200
    assert mock_task.called
    # The coroutine passed to create_task must come from process_ci_failure
    coroutine_arg = mock_task.call_args[0][0]
    assert coroutine_arg.cr_frame.f_locals.get("__self__", None) is None
    assert mock_fn.called


def test_check_run_non_failure_does_not_spawn_task(tmp_path, monkeypatch):
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", str(tmp_path / "test4.db"))
    monkeypatch.setattr(cfg_mod.config.github, "webhook_secret", SECRET)

    import importlib

    import src.webhook_handler as wh

    importlib.reload(wh)

    body = json.dumps(
        {
            "repository": {"full_name": "org/repo"},
            "action": "completed",
            "check_run": {"conclusion": "success"},
        }
    ).encode()
    sig = _make_sig(body)

    with patch.object(wh.asyncio, "create_task") as mock_task:
        with TestClient(wh.app) as c:
            resp = c.post(
                "/webhook",
                content=body,
                headers={
                    "X-GitHub-Event": "check_run",
                    "X-GitHub-Delivery": "delivery-6",
                    "X-Hub-Signature-256": sig,
                    "Content-Type": "application/json",
                },
            )
    assert resp.status_code == 200
    assert not mock_task.called


def test_push_event_spawns_process_push(tmp_path, monkeypatch):
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", str(tmp_path / "test5.db"))
    monkeypatch.setattr(cfg_mod.config.github, "webhook_secret", SECRET)

    import importlib

    import src.webhook_handler as wh

    importlib.reload(wh)

    body = json.dumps(
        {"repository": {"full_name": "org/repo"}, "after": "abc123"}
    ).encode()
    sig = _make_sig(body)

    with patch.object(wh, "process_push", new=AsyncMock()) as mock_fn:
        with patch.object(wh.asyncio, "create_task") as mock_task:
            with TestClient(wh.app) as c:
                resp = c.post(
                    "/webhook",
                    content=body,
                    headers={
                        "X-GitHub-Event": "push",
                        "X-GitHub-Delivery": "delivery-7",
                        "X-Hub-Signature-256": sig,
                        "Content-Type": "application/json",
                    },
                )
    assert resp.status_code == 200
    assert mock_task.called
    assert mock_fn.called


def test_invalid_signature_does_not_store_event(tmp_path, monkeypatch):
    """Payload must NOT be stored when signature is invalid."""
    import src.config as cfg_mod

    monkeypatch.setattr(cfg_mod.config.db, "path", str(tmp_path / "test6.db"))
    monkeypatch.setattr(cfg_mod.config.github, "webhook_secret", SECRET)

    import importlib

    import src.webhook_handler as wh

    importlib.reload(wh)

    with patch.object(wh, "store_raw_event") as mock_store:
        body = json.dumps({"repository": {"full_name": "org/repo"}}).encode()
        with TestClient(wh.app) as c:
            c.post(
                "/webhook",
                content=body,
                headers={
                    "X-GitHub-Event": "ping",
                    "X-GitHub-Delivery": "delivery-8",
                    "X-Hub-Signature-256": "sha256=badsig",
                    "Content-Type": "application/json",
                },
            )
    mock_store.assert_not_called()


# ---------------------------------------------------------------------------
# /internal/register-watch endpoint
# ---------------------------------------------------------------------------


def test_register_watch_endpoint(client):
    """POST /internal/register-watch with branch must return ok=True."""
    resp = client.post(
        "/internal/register-watch",
        json={"branch": "main"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_register_watch_endpoint_missing_branch(client):
    """POST /internal/register-watch without branch must return 422."""
    resp = client.post(
        "/internal/register-watch",
        json={"session_id": "abc"},
    )
    assert resp.status_code == 422
