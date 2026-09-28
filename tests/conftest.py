from __future__ import annotations

import pytest

from src.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        port=8080,
        public_base_url="https://mcp.example.test",
        mcp_shared_secret="test-secret",
        mcp_token_signing_key="test-signing-key-0123456789abcdef",
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
        google_refresh_token="test-refresh-token",
    )
