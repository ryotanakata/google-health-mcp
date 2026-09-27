from __future__ import annotations

import asyncio

import google.auth.transport.requests
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from src.config import Settings
from src.constants import GOOGLE_HEALTH_SCOPES, GOOGLE_TOKEN_URI


class RefreshTokenExpiredError(RuntimeError):
    pass


class GoogleAuthManager:
    def __init__(self, settings: Settings) -> None:
        self._credentials = Credentials(
            token=None,
            refresh_token=settings.google_refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            scopes=GOOGLE_HEALTH_SCOPES,
        )
        # 並列リクエストが同時に期限切れを検知しても、refresh を1回にまとめる
        self._refresh_lock = asyncio.Lock()

    async def get_access_token(self) -> str:
        if not self._credentials.valid:
            async with self._refresh_lock:
                if not self._credentials.valid:
                    # refresh は同期 HTTP のため、イベントループを止めないよう別スレッドで行う
                    await asyncio.to_thread(self._refresh)
        return self._credentials.token

    def _refresh(self) -> None:
        request = google.auth.transport.requests.Request()
        try:
            self._credentials.refresh(request)
        except RefreshError as exc:
            raise RefreshTokenExpiredError(
                "refresh_tokenが失効しています。auth_setup.pyを再実行し、"
                "新しいrefresh_tokenをSecret Managerに登録してください。"
            ) from exc
