from __future__ import annotations

import pytest

from src.config import ConfigError, Settings

SIGNING_KEY = "k" * 32


def test_from_env_missing_required(monkeypatch):
    monkeypatch.delenv("MCP_SHARED_SECRET", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://mcp.example.test/")
    monkeypatch.setenv("MCP_SHARED_SECRET", "s")
    monkeypatch.setenv("MCP_TOKEN_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "token")
    monkeypatch.delenv("PORT", raising=False)

    settings = Settings.from_env()

    assert settings.mcp_shared_secret == "s"
    assert settings.mcp_token_signing_key == SIGNING_KEY
    assert settings.google_client_id == "id"
    assert settings.port == 8080
    assert settings.public_base_url == "https://mcp.example.test"


def test_from_env_rejects_base_url_with_path(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://mcp.example.test/mcp")
    monkeypatch.setenv("MCP_SHARED_SECRET", "s")
    monkeypatch.setenv("MCP_TOKEN_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "token")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_from_env_rejects_short_signing_key(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://mcp.example.test")
    monkeypatch.setenv("MCP_SHARED_SECRET", "s")
    monkeypatch.setenv("MCP_TOKEN_SIGNING_KEY", "k" * 31)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "token")
    with pytest.raises(ConfigError, match="MCP_TOKEN_SIGNING_KEY"):
        Settings.from_env()
