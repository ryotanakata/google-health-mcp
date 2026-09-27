from __future__ import annotations

import base64
import contextlib
import hashlib
import re
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

PUBLIC_BASE_URL = "https://mcp.example.test"
PASSPHRASE = "expected-secret"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}


@pytest.fixture
def main_module(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", PUBLIC_BASE_URL)
    monkeypatch.setenv("MCP_SHARED_SECRET", PASSPHRASE)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "token")
    # src.main はモジュールロード時に Settings.from_env() を評価するため、
    # 既にインポート済みならモジュールキャッシュを外して再読み込みする。
    sys.modules.pop("src.main", None)
    import src.main as module

    yield module
    sys.modules.pop("src.main", None)


@contextlib.asynccontextmanager
async def _serve(app, base_url: str = PUBLIC_BASE_URL):
    # pytest-asyncio の async fixture だと lifespan の開始と終了が別タスクになり
    # anyio が失敗するため、
    # テスト本体と同じタスク内で起動・停止する
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client:
            yield client


async def _register(http, **overrides) -> dict:
    body = {
        "redirect_uris": [CLAUDE_CALLBACK],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        **overrides,
    }
    response = await http.post("/register", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _authorize(http, client_id: str) -> tuple[str, str]:
    """認可リクエストを送り、(ログイン画面のURL, code_verifier) を返す。"""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    response = await http.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CLAUDE_CALLBACK,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
            "state": "state-123",
        },
    )
    assert response.status_code == 302, response.text
    return response.headers["location"], verifier


async def _login(http, login_url: str, passphrase: str):
    page = await http.get(login_url)
    assert page.status_code == 200
    login_request = re.search(r'name="request" value="([^"]+)"', page.text).group(1)
    return await http.post(
        "/oauth/login", data={"request": login_request, "passphrase": passphrase}
    )


async def _obtain_tokens(http, client: dict) -> dict:
    login_url, verifier = await _authorize(http, client["client_id"])
    redirect = await _login(http, login_url, PASSPHRASE)
    assert redirect.status_code == 302
    query = parse_qs(urlparse(redirect.headers["location"]).query)
    assert query["state"] == ["state-123"]
    token = await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": CLAUDE_CALLBACK,
            "client_id": client["client_id"],
            "code_verifier": verifier,
            **({"client_secret": client["client_secret"]} if client.get("client_secret") else {}),
        },
    )
    assert token.status_code == 200, token.text
    return {**token.json(), "_code": query["code"][0], "_verifier": verifier}


async def test_mcp_requires_auth_and_points_to_resource_metadata(main_module):
    async with _serve(main_module.app) as http:
        response = await http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]

        metadata = await http.get("/.well-known/oauth-protected-resource/mcp")
        assert metadata.status_code == 200
        assert metadata.json()["resource"] == f"{PUBLIC_BASE_URL}/mcp"



async def test_rejects_invalid_bearer_token(main_module):
    async with _serve(main_module.app) as http:
        response = await http.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401



async def test_full_oauth_flow_allows_mcp_access(main_module):
    async with _serve(main_module.app) as http:
        client = await _register(http)
        tokens = await _obtain_tokens(http, client)

        response = await http.post(
            "/mcp",
            json=INITIALIZE,
            headers={**MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["result"]["serverInfo"]["name"] == "google-health"

        tools = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={
                **MCP_HEADERS,
                "Authorization": f"Bearer {tokens['access_token']}",
                "MCP-Protocol-Version": "2025-06-18",
            },
        )
        assert tools.status_code == 200, tools.text
        assert {tool["name"] for tool in tools.json()["result"]["tools"]} == {
            "get_daily_activity",
            "get_sleep_log",
            "get_heart_rate_summary",
            "get_exercise_history",
        }
        # models.py の TypedDict から戻り値のスキーマが生成され、Claude に伝わること
        listed = tools.json()["result"]["tools"]
        schemas = {tool["name"]: tool.get("outputSchema") for tool in listed}
        assert "resting_heart_rate" in schemas["get_heart_rate_summary"]["properties"]



async def test_oauth_flow_with_client_secret(main_module):
    async with _serve(main_module.app) as http:
        client = await _register(http, token_endpoint_auth_method="client_secret_post")
        assert client["client_secret"]
        tokens = await _obtain_tokens(http, client)
        assert tokens["access_token"]



async def test_refresh_token_grant(main_module):
    async with _serve(main_module.app) as http:
        client = await _register(http)
        tokens = await _obtain_tokens(http, client)

        refreshed = await http.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client["client_id"],
            },
        )

        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["access_token"]



async def test_authorization_code_cannot_be_reused(main_module):
    async with _serve(main_module.app) as http:
        client = await _register(http)
        tokens = await _obtain_tokens(http, client)

        reused = await http.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": tokens["_code"],
                "redirect_uri": CLAUDE_CALLBACK,
                "client_id": client["client_id"],
                "code_verifier": tokens["_verifier"],
            },
        )

        assert reused.status_code == 400



async def test_wrong_passphrase_is_rejected(main_module, monkeypatch):
    async with _serve(main_module.app) as http:
        monkeypatch.setattr("src.claude_auth.OAUTH_FAILED_LOGIN_DELAY_SECONDS", 0)
        client = await _register(http)
        login_url, _ = await _authorize(http, client["client_id"])

        response = await _login(http, login_url, "wrong")

        assert response.status_code == 401
        assert "location" not in response.headers



async def test_registration_rejects_unknown_redirect_uri(main_module):
    async with _serve(main_module.app) as http:
        response = await http.post(
            "/register",
            json={
                "redirect_uris": ["https://evil.example/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        assert response.status_code == 400



async def test_mcp_rejects_unexpected_host(main_module):
    async with _serve(main_module.app) as http:
        client = await _register(http)
        tokens = await _obtain_tokens(http, client)
        transport = ASGITransport(app=main_module.app)
        other = AsyncClient(transport=transport, base_url="https://evil.example")
        async with other:
            response = await other.post(
                "/mcp",
                json=INITIALIZE,
                headers={**MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"},
            )
    assert response.status_code == 421
