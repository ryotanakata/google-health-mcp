from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from src.google_auth import GoogleAuthManager, RefreshTokenExpiredError


async def test_get_access_token_wraps_refresh_error(settings):
    manager = GoogleAuthManager(settings)
    with (
        patch.object(type(manager._credentials), "valid", False),
        patch.object(manager._credentials, "refresh", side_effect=RefreshError("expired")),
    ):
        with pytest.raises(RefreshTokenExpiredError):
            await manager.get_access_token()


async def test_get_access_token_returns_token_when_already_valid(settings):
    manager = GoogleAuthManager(settings)
    manager._credentials.token = "existing-token"
    with (
        patch.object(type(manager._credentials), "valid", True),
        patch.object(manager._credentials, "refresh") as refresh,
    ):
        assert await manager.get_access_token() == "existing-token"
    refresh.assert_not_called()


async def test_refresh_runs_once_outside_event_loop_thread(settings):
    manager = GoogleAuthManager(settings)
    loop_thread = threading.current_thread()
    refresh_threads: list[threading.Thread] = []

    def refresh(request):
        refresh_threads.append(threading.current_thread())
        # 他のリクエストが期限切れを検知して待つ間に更新が終わる状況を作る
        time.sleep(0.05)
        manager._credentials.token = "new-token"

    with (
        patch.object(Credentials, "valid", property(lambda self: self.token is not None)),
        patch.object(manager._credentials, "refresh", side_effect=refresh),
    ):
        tokens = await asyncio.gather(*(manager.get_access_token() for _ in range(4)))

    assert tokens == ["new-token"] * 4
    assert len(refresh_threads) == 1
    assert refresh_threads[0] is not loop_thread
