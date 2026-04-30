"""GitHub App authentication: JWT generation and installation token exchange."""

from __future__ import annotations

import time

import jwt
from github import Github, GithubIntegration

from src.config import config


def _read_private_key() -> str:
    """Read the GitHub App private key from disk."""
    with open(config.github.private_key_path) as f:
        return f.read()


def _generate_jwt() -> str:
    """Sign a short-lived JWT with the GitHub App private key."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # 60s in the past (clock-skew buffer)
        "exp": now + (10 * 60),  # valid 10 minutes (GitHub max)
        "iss": config.github.app_id,
    }
    return jwt.encode(payload, _read_private_key(), algorithm="RS256")


def get_github_client() -> Github:
    """
    Return an authenticated Github client using the App installation token.
    Generates a fresh token on each call — tokens are valid 1 hour.
    Layer 1 (low volume): no caching needed.
    Layer 2 upgrade: add an LRU cache with TTL to avoid regenerating tokens.
    """
    integration = GithubIntegration(
        config.github.app_id,
        _read_private_key(),
    )
    token = integration.get_access_token(int(config.github.installation_id))
    return Github(token.token)
