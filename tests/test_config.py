"""Tests for src/config.py typed AppConfig loader."""

from __future__ import annotations

import textwrap

import pytest


MINIMAL_ENV = {
    "GITHUB_APP_ID": "12345",
    "GITHUB_WEBHOOK_SECRET": "test-secret",
    "GITHUB_INSTALLATION_ID": "67890",
}


def _write_config(tmp_path, extra: str = "") -> str:
    content = (
        textwrap.dedent("""\
        github:
          app_id: ""
          private_key_path: ".keys/app.pem"
          webhook_secret: ""
          installation_id: ""

        server:
          host: "0.0.0.0"
          port: 8080

        db:
          path: "data/events.db"

        enrichment:
          max_actionable_failures: 5
          test_body:
            enabled: true
            max_lines: 30
          likely_cause:
            enabled: true
            provider: ollama
            base_url: "http://localhost:11434"
            model: deepseek-coder-v2
            max_react_steps: 4
            timeout_seconds: 10
            fallback_on_timeout: true
            framework: langgraph

        circuit_breaker:
          max_attempts: 3
    """)
        + extra
    )
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(content)
    return str(cfg_path)


def test_load_config_success(tmp_path, monkeypatch):
    """load_config returns AppConfig when all required env vars are set."""
    cfg_path = _write_config(tmp_path)
    for k, v in MINIMAL_ENV.items():
        monkeypatch.setenv(k, v)

    from src.config import load_config

    cfg = load_config(cfg_path)

    assert cfg.github.app_id == "12345"
    assert cfg.github.webhook_secret == "test-secret"
    assert cfg.github.installation_id == "67890"
    assert cfg.github.private_key_path == ".keys/app.pem"
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8080
    assert cfg.db.path == "data/events.db"
    assert cfg.enrichment.max_actionable_failures == 5
    assert cfg.enrichment.test_body.enabled is True
    assert cfg.enrichment.test_body.max_lines == 30
    assert cfg.circuit_breaker.max_attempts == 3


def test_load_config_model_from_yml(tmp_path, monkeypatch):
    """config.enrichment.likely_cause.model returns 'deepseek-coder-v2' from config.yml."""
    cfg_path = _write_config(tmp_path)
    for k, v in MINIMAL_ENV.items():
        monkeypatch.setenv(k, v)

    from src.config import load_config

    cfg = load_config(cfg_path)
    assert cfg.enrichment.likely_cause.model == "deepseek-coder-v2"


def test_load_config_ollama_base_url_override(tmp_path, monkeypatch):
    """OLLAMA_BASE_URL env var overrides config.yml base_url."""
    cfg_path = _write_config(tmp_path)
    for k, v in MINIMAL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://my-ollama:11434")

    from src.config import load_config

    cfg = load_config(cfg_path)
    assert cfg.enrichment.likely_cause.base_url == "http://my-ollama:11434"


def test_load_config_missing_app_id(tmp_path, monkeypatch):
    """Raises ValueError naming GITHUB_APP_ID when it is absent."""
    cfg_path = _write_config(tmp_path)
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("GITHUB_INSTALLATION_ID", "1")

    from src.config import load_config

    with pytest.raises(ValueError, match="GITHUB_APP_ID"):
        load_config(cfg_path)


def test_load_config_missing_webhook_secret(tmp_path, monkeypatch):
    """Raises ValueError naming GITHUB_WEBHOOK_SECRET when it is absent."""
    cfg_path = _write_config(tmp_path)
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("GITHUB_INSTALLATION_ID", "1")

    from src.config import load_config

    with pytest.raises(ValueError, match="GITHUB_WEBHOOK_SECRET"):
        load_config(cfg_path)


def test_load_config_missing_installation_id(tmp_path, monkeypatch):
    """Raises ValueError naming GITHUB_INSTALLATION_ID when it is absent."""
    cfg_path = _write_config(tmp_path)
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.delenv("GITHUB_INSTALLATION_ID", raising=False)

    from src.config import load_config

    with pytest.raises(ValueError, match="GITHUB_INSTALLATION_ID"):
        load_config(cfg_path)


def test_load_config_multiple_missing(tmp_path, monkeypatch):
    """Raises ValueError listing all missing required env vars."""
    cfg_path = _write_config(tmp_path)
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("GITHUB_INSTALLATION_ID", raising=False)

    from src.config import load_config

    with pytest.raises(ValueError, match="Missing required config"):
        load_config(cfg_path)
