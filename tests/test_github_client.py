"""Tests for src/github_client.py — JWT generation and installation token flow."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
import pytest


# ---------------------------------------------------------------------------
# _generate_jwt
# ---------------------------------------------------------------------------

FAKE_RSA_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA2a2rwplBQLzHPZe5TNJT5s6bSNElx7gcKSzstVKUNBDoubQl
gDSBRbFOHDm3Thcg9X08xBSDpfULZA0EUBaIMgWVLFSSBipH0q1xfX+mXvPnVMFD
SGV4fEsmSPFGzFMCEnV1HBKV3IkiyjKA9YF7eJUMCE5yW4EYQ3T3dsf3PJo5pKRw
WoG1/ZRTY3oQVeYp13A8KJHLgR0cC7E1j6oJGNUDi5GGoBZlhYbKdIh5hV3Xtpqk
V+kQfT1pNUYfcfGBE5hB1B8hQ8fPGHDOp8TKWV5VvU1K0Sz5JhRTvBxLNrjFv6e
m7v3xsX3bTzw2TNyMjFW18qpUKUFzgSjxJEZPwIDAQABAoIBAHl0bSb8FQ0j+ZFx
rKe4LxzNRMHuoZtCbITXl2FNUjXcZfIvBJO7YKm0lrQqGRJ7QLXJ5y0j6BQRXK9
YQjwdUt8cOnE1PKp9A9J/gjHl7bUZfFSuZlWBVCJ9mH4j9BdWH6LPIFpL0t9vFjR
qVqIw2Y1yCqPL5DNPJPjO5cj2KXMc2pXhHv5vMbCx7c2fVG7/B3y7S/y+9sRLHOM
yMhQjM1MyvFVD1b5aUKb3GQZMU5O5PEREkBcqV3tX8dV7YQOG7bBp9f3M5bpfLX6
c+1vK6oF5SnH5N0V3EkVS9jJW7kh9FBBvbbgFnOb6FY/a1VTHBXPObjqKFW3nlpz
YKDi8kECgYEA7sKiVNmNl6E9dA0Y6OljF9V9E0MRUGz7k3Yg3o0YqfApWTG4Kf/P
FnnUCfk2qNNH3/a1/r3KXCZ9I2YfKZ8C6UF0P8B+DQSL5LmPVNrKjUmAzS0G6WH0
5vJh9L3m4mZ2cNqV4Z6Y9UtGlq4K5U5r9FJBL6Yo39FEPQ3p1SsCgYEA6Y5b6Hhf
nRlO4vJnp5E5Kt3M4k2qPLh5M2xR6V0fHzJ3gH0N9XqXH5M3RKylcJFzT5pjFP1Y
sR3gPLV6mIL2SrZeHrBEnfFYrPElFjJNnZTi9RKqhT6YP+qB3KHSS2mHoVq2h0Jf
y5EKr+l1MQaFUQb7R2jT0UvWrlNb9bpVS0kCgYBrG2o7Kw9aD1o7qg8Xtz4VjFCJ
vlLs6zl5kFKa0k5jXpISOcR98G3mI4J2XY3G7PWRM3gF9Gr1y2B0h0Q5gP0LxDQ4
VkM7TjN0LS0g4HVLyFcQm7d9rF4XM7m5j8j8pK6k5K1U3s0eW5q8HZ5T/yKBaTfN
VnDMpfX0TkEJG9L7kwKBgBXZqb+XJo7VE1J6u9m0r8L2X1Y5lX0VzPNlz0MFqJ8G
9yCUt0u8E6LPMz6RYbEI6DYLMBYQLsNVqg3xm/3tHb7M5mMdSv0UfCGANi8XF0TS
+MoFV7k/xFOJ8nOh2R6qgGp5lLn0eFxoQK+T4/I5M5jHGJqU3N9f4JFJn97ZAoGB
AKalfr5Y9T5Zao8sFMBrXbHqDpjpfA2X5Wr7/Mj4B8KV7wOk5Y4+sHtF4+3x3vwI
0lq7Y5K3aTi5T1mj5l3K1Q6z1o2a9dQ8bpnYv7cN1R0HwQVbdMFEZBd3mhTX2h3t
m7TmZsJiYeJ3bT9L7kF6qVJpYQZ3m4K5Y6nC4L5m
-----END RSA PRIVATE KEY-----"""


def _make_rsa_key() -> bytes:
    """Generate a real RSA key for testing."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture()
def rsa_private_key_pem(tmp_path) -> tuple[str, bytes]:
    """Write a real RSA private key to a temp file; return (path, pem_bytes)."""
    pem = _make_rsa_key()
    key_file = tmp_path / "app.pem"
    key_file.write_bytes(pem)
    return str(key_file), pem


def test_generate_jwt_claims(rsa_private_key_pem, monkeypatch):
    """_generate_jwt produces a JWT with correct iat/exp/iss claims."""
    key_path, pem = rsa_private_key_pem

    from src import config as config_module

    monkeypatch.setattr(config_module.config.github, "private_key_path", key_path)
    monkeypatch.setattr(config_module.config.github, "app_id", "42")

    from src.github_client import _generate_jwt

    before = int(time.time())
    token = _generate_jwt()
    after = int(time.time())

    # Decode without verification to inspect claims
    claims = jwt.decode(token, options={"verify_signature": False})

    assert claims["iss"] == "42"
    # iat should be ~60 seconds before "now"
    assert before - 61 <= claims["iat"] <= after - 59
    # exp should be ~10 minutes after "now"
    assert before + 539 <= claims["exp"] <= after + 601


def test_generate_jwt_is_rs256(rsa_private_key_pem, monkeypatch):
    """_generate_jwt produces a token signed with RS256."""
    key_path, pem = rsa_private_key_pem

    from src import config as config_module

    monkeypatch.setattr(config_module.config.github, "private_key_path", key_path)
    monkeypatch.setattr(config_module.config.github, "app_id", "99")

    from src.github_client import _generate_jwt

    token = _generate_jwt()
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"


def test_get_github_client_returns_github_instance(rsa_private_key_pem, monkeypatch):
    """get_github_client() returns a Github instance using an installation token."""
    key_path, pem = rsa_private_key_pem

    from src import config as config_module

    monkeypatch.setattr(config_module.config.github, "private_key_path", key_path)
    monkeypatch.setattr(config_module.config.github, "app_id", "42")
    monkeypatch.setattr(config_module.config.github, "installation_id", "1234")

    fake_token = MagicMock()
    fake_token.token = "ghs_test_token"

    mock_integration = MagicMock()
    mock_integration.get_access_token.return_value = fake_token

    with patch(
        "src.github_client.GithubIntegration", return_value=mock_integration
    ) as mock_cls:
        from github import Github

        from src.github_client import get_github_client

        client = get_github_client()

        mock_cls.assert_called_once_with("42", pem.decode())
        mock_integration.get_access_token.assert_called_once_with(1234)
        assert isinstance(client, Github)


def test_get_github_client_passes_token_to_github(rsa_private_key_pem, monkeypatch):
    """get_github_client() passes the installation token to Github constructor."""
    key_path, pem = rsa_private_key_pem

    from src import config as config_module

    monkeypatch.setattr(config_module.config.github, "private_key_path", key_path)
    monkeypatch.setattr(config_module.config.github, "app_id", "42")
    monkeypatch.setattr(config_module.config.github, "installation_id", "1234")

    fake_token = MagicMock()
    fake_token.token = "ghs_specific_token"

    mock_integration = MagicMock()
    mock_integration.get_access_token.return_value = fake_token

    with patch("src.github_client.GithubIntegration", return_value=mock_integration):
        with patch("src.github_client.Github") as mock_github_cls:
            from src.github_client import get_github_client

            get_github_client()
            mock_github_cls.assert_called_once_with("ghs_specific_token")
