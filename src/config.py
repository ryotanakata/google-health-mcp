from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from src.constants import OAUTH_SIGNING_KEY_MIN_LENGTH


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    port: int
    public_base_url: str
    mcp_shared_secret: str
    mcp_token_signing_key: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            port=int(os.environ.get("PORT", "8080")),
            public_base_url=cls._require_base_url("PUBLIC_BASE_URL"),
            mcp_shared_secret=cls._require("MCP_SHARED_SECRET"),
            mcp_token_signing_key=cls._require_signing_key("MCP_TOKEN_SIGNING_KEY"),
            google_client_id=cls._require("GOOGLE_CLIENT_ID"),
            google_client_secret=cls._require("GOOGLE_CLIENT_SECRET"),
            google_refresh_token=cls._require("GOOGLE_REFRESH_TOKEN"),
        )

    @staticmethod
    def _require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ConfigError(f"環境変数 {name} が設定されていません")
        return value

    @staticmethod
    def _require_signing_key(name: str) -> str:
        value = Settings._require(name)
        if len(value) < OAUTH_SIGNING_KEY_MIN_LENGTH:
            raise ConfigError(
                f"環境変数 {name} は {OAUTH_SIGNING_KEY_MIN_LENGTH} 文字以上の"
                "ランダム値にしてください（例: openssl rand -hex 32）"
            )
        return value

    @staticmethod
    def _require_base_url(name: str) -> str:
        value = Settings._require(name).rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path:
            raise ConfigError(
                f"環境変数 {name} はパスを含まないオリジン（例: https://example.a.run.app）で指定してください"
            )
        return value
