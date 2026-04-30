"""Pytest configuration: set required env vars so src.config can be imported."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _set_required_env_vars():
    """Ensure required env vars exist so the module-level config = load_config() succeeds."""
    defaults = {
        "GITHUB_APP_ID": "test-app-id",
        "GITHUB_WEBHOOK_SECRET": "test-webhook-secret",
        "GITHUB_INSTALLATION_ID": "test-installation-id",
    }
    original = {}
    for key, value in defaults.items():
        original[key] = os.environ.get(key)
        if not os.environ.get(key):
            os.environ[key] = value

    yield

    for key, original_value in original.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value
