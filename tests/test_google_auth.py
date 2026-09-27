from __future__ import annotations

from unittest.mock import patch

import pytest
from google.auth.exceptions import RefreshError

from src.google_auth import GoogleAuthManager, RefreshTokenExpiredError


def test_get_access_token_wraps_refresh_error(settings):
    manager = GoogleAuthManager(settings)
    with (
        patch.object(type(manager._credentials), "valid", False),
        patch.object(manager._credentials, "refresh", side_effect=RefreshError("expired")),
    ):
        with pytest.raises(RefreshTokenExpiredError):
            manager.get_access_token()


def test_get_access_token_returns_token_when_already_valid(settings):
    manager = GoogleAuthManager(settings)
    manager._credentials.token = "existing-token"
    with patch.object(type(manager._credentials), "valid", True):
        assert manager.get_access_token() == "existing-token"
