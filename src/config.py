"""Typed AppConfig loader: merges config.yml with .env secret overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass
class GitHubConfig:
    app_id: str
    private_key_path: str
    webhook_secret: str
    installation_id: str


@dataclass
class TestBodyConfig:
    enabled: bool
    max_lines: int


@dataclass
class LikelyCauseConfig:
    enabled: bool
    provider: str
    base_url: str
    model: str
    max_react_steps: int
    timeout_seconds: int
    fallback_on_timeout: bool
    framework: str


@dataclass
class EnrichmentConfig:
    max_actionable_failures: int
    test_body: TestBodyConfig
    likely_cause: LikelyCauseConfig


@dataclass
class CircuitBreakerConfig:
    max_attempts: int


@dataclass
class ServerConfig:
    host: str
    port: int


@dataclass
class DBConfig:
    path: str


@dataclass
class AppConfig:
    github: GitHubConfig
    server: ServerConfig
    db: DBConfig
    enrichment: EnrichmentConfig
    circuit_breaker: CircuitBreakerConfig


def load_config(path: str = "config.yml") -> AppConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    gh = raw["github"]
    gh["app_id"] = os.getenv("GITHUB_APP_ID", gh.get("app_id", ""))
    gh["private_key_path"] = os.getenv(
        "GITHUB_PRIVATE_KEY_PATH", gh.get("private_key_path", "")
    )
    gh["webhook_secret"] = os.getenv(
        "GITHUB_WEBHOOK_SECRET", gh.get("webhook_secret", "")
    )
    gh["installation_id"] = os.getenv(
        "GITHUB_INSTALLATION_ID", gh.get("installation_id", "")
    )

    lc = raw["enrichment"]["likely_cause"]
    lc["base_url"] = os.getenv(
        "OLLAMA_BASE_URL", lc.get("base_url", "http://localhost:11434")
    )

    required = {
        "GITHUB_APP_ID": gh["app_id"],
        "GITHUB_WEBHOOK_SECRET": gh["webhook_secret"],
        "GITHUB_INSTALLATION_ID": gh["installation_id"],
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(f"Missing required config: {', '.join(missing)}")

    return AppConfig(
        github=GitHubConfig(**gh),
        server=ServerConfig(**raw["server"]),
        db=DBConfig(**raw["db"]),
        enrichment=EnrichmentConfig(
            max_actionable_failures=raw["enrichment"]["max_actionable_failures"],
            test_body=TestBodyConfig(**raw["enrichment"]["test_body"]),
            likely_cause=LikelyCauseConfig(**lc),
        ),
        circuit_breaker=CircuitBreakerConfig(**raw["circuit_breaker"]),
    )


config = load_config()
