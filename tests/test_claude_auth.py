from __future__ import annotations

from mcp.shared.auth import OAuthClientInformationFull

from src.claude_auth import ClaudeOAuthProvider

MALICIOUS = '"><script>alert(1)</script>'


def test_login_form_escapes_inserted_values():
    response = ClaudeOAuthProvider._login_form(MALICIOUS, error=MALICIOUS)
    body = response.body.decode()

    assert "<script>" not in body
    assert "&quot;&gt;&lt;script&gt;" in body
    assert 'action="/oauth/login"' in body


def test_login_form_without_error_has_no_error_block():
    body = ClaudeOAuthProvider._login_form("request-token").body.decode()

    assert 'class="error"' not in body
    assert 'value="request-token"' in body


def test_login_error_page_escapes_message():
    response = ClaudeOAuthProvider._login_error_page(MALICIOUS)

    assert response.status_code == 400
    assert "<script>" not in response.body.decode()


def test_is_allowed_redirect_uri():
    assert ClaudeOAuthProvider.is_allowed_redirect_uri("https://claude.ai/api/mcp/auth_callback")
    assert ClaudeOAuthProvider.is_allowed_redirect_uri("http://localhost:3118/callback")
    assert ClaudeOAuthProvider.is_allowed_redirect_uri("http://127.0.0.1:50000/callback")
    assert not ClaudeOAuthProvider.is_allowed_redirect_uri("https://evil.example/callback")
    assert not ClaudeOAuthProvider.is_allowed_redirect_uri("https://localhost/callback")


async def test_signing_key_is_independent_of_passphrase():
    issuer = "https://mcp.example.test"
    provider = ClaudeOAuthProvider("passphrase", "signing-key-a" * 3, issuer)
    client_info = OAuthClientInformationFull(
        client_id="sdk-assigned",
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client_info)

    same_key = ClaudeOAuthProvider("other-passphrase", "signing-key-a" * 3, issuer)
    same_passphrase = ClaudeOAuthProvider("passphrase", "signing-key-b" * 3, issuer)

    assert await same_key.get_client(client_info.client_id) is not None
    assert await same_passphrase.get_client(client_info.client_id) is None
