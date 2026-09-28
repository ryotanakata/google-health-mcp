"""Claude.ai の個人アカウントは固定 Bearer トークンでのコネクタ接続に対応していない
（static_headers は一部組織向けベータ）ため、MCP サーバー自身が認可サーバーを兼ねる。

- 利用者は本人1人。同意画面で MCP_SHARED_SECRET をパスフレーズとして入力できた人にだけ
  トークンを発行する
- Cloud Run はインスタンスの再起動・複数起動があるため、クライアント登録情報・認可コード・
  トークンはすべて MCP_TOKEN_SIGNING_KEY から導出した鍵で署名した JWT とし、サーバー側に
  状態を持たない（DB 不要）
- 署名鍵をパスフレーズから導出しない。署名済みの client_id は誰でも登録で入手できるため、
  人が入力できる程度のパスフレーズを鍵にすると、オフラインの総当たりで鍵が割れる
- ステートレスの代償として、発行済みトークンを個別に失効させることはできない。
  全トークンを無効にしたいときは MCP_TOKEN_SIGNING_KEY を変更する
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import secrets
import time
from string import Template
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from src.constants import (
    CLAUDE_CALLBACK_URL,
    LOGIN_ERROR_TEMPLATE,
    LOGIN_FORM_TEMPLATE,
    LOGIN_PAGE_HEADERS,
    LOGIN_PAGE_TEMPLATE,
    NO_STORE_HEADERS,
    OAUTH_ACCESS_TOKEN_TTL_SECONDS,
    OAUTH_AUTHORIZATION_CODE_TTL_SECONDS,
    OAUTH_FAILED_LOGIN_DELAY_SECONDS,
    OAUTH_INVALID_REDIRECT_URI_MESSAGE,
    OAUTH_INVALID_REQUEST_MESSAGE,
    OAUTH_JWT_ALGORITHM,
    OAUTH_LOGIN_CLAIMS,
    OAUTH_LOGIN_PATH,
    OAUTH_LOGIN_REQUEST_TTL_SECONDS,
    OAUTH_LOOPBACK_HOSTS,
    OAUTH_REFRESH_TOKEN_TTL_SECONDS,
    OAUTH_SCOPE,
    OAUTH_TOKEN_SUBJECT,
    OAUTH_WRONG_PASSPHRASE_MESSAGE,
)


class ClaudeOAuthProvider:
    """mcp.server.auth.provider.OAuthAuthorizationServerProvider を継承せずに満たす実装。"""

    def __init__(self, shared_secret: str, signing_key: str, issuer_url: str) -> None:
        self._passphrase = shared_secret.encode()
        self._signing_key = hmac.new(
            signing_key.encode(), b"google-health-mcp/oauth-signing", hashlib.sha256
        ).digest()
        self._issuer = issuer_url.rstrip("/")
        # 認可コードの再利用防止。インスタンスをまたぐと効かないが、PKCE と5分の有効期限で補う
        self._used_codes: dict[str, float] = {}

    def _sign(self, typ: str, claims: dict[str, Any], ttl_seconds: int | None) -> str:
        now = int(time.time())
        payload = {**claims, "typ": typ, "iss": self._issuer, "iat": now}
        if ttl_seconds is not None:
            payload["exp"] = now + ttl_seconds
        return jwt.encode(payload, self._signing_key, algorithm=OAUTH_JWT_ALGORITHM)

    def _verify(self, typ: str, token: str) -> dict[str, Any] | None:
        try:
            claims = jwt.decode(
                token, self._signing_key, algorithms=[OAUTH_JWT_ALGORITHM], issuer=self._issuer
            )
        except jwt.PyJWTError:
            return None
        return claims if claims.get("typ") == typ else None

    def _client_secret_for(self, client_id: str) -> str:
        return hmac.new(
            self._signing_key, b"client-secret:" + client_id.encode(), hashlib.sha256
        ).hexdigest()

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        redirect_uris = [str(uri) for uri in client_info.redirect_uris or []]
        if not redirect_uris or not all(map(self.is_allowed_redirect_uri, redirect_uris)):
            raise RegistrationError("invalid_redirect_uri", OAUTH_INVALID_REDIRECT_URI_MESSAGE)
        # SDK が採番した client_id を、登録内容を署名で封じ込めた値に差し替える
        # （RFC 7591 上、サーバーが登録値を置き換えることは認められている）。
        # 登録ハンドラはこのオブジェクトをそのままレスポンスに使うため、差し替えがクライアントに届く
        client_id = self._sign(
            "client",
            {
                "redirect_uris": redirect_uris,
                "auth_method": client_info.token_endpoint_auth_method,
                "scope": client_info.scope,
            },
            ttl_seconds=None,
        )
        client_info.client_id = client_id
        if client_info.client_secret is not None:
            client_info.client_secret = self._client_secret_for(client_id)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        claims = self._verify("client", client_id)
        if claims is None:
            return None
        auth_method = claims["auth_method"]
        has_secret = auth_method != "none"
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=self._client_secret_for(client_id) if has_secret else None,
            client_secret_expires_at=0 if has_secret else None,
            redirect_uris=claims["redirect_uris"],
            token_endpoint_auth_method=auth_method,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=claims.get("scope"),
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        scopes = params.scopes or (client.scope.split() if client.scope else [OAUTH_SCOPE])
        login_request = self._sign(
            "login",
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_explicit": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "scopes": scopes,
                "state": params.state,
                "resource": params.resource,
            },
            OAUTH_LOGIN_REQUEST_TTL_SECONDS,
        )
        return f"{self._issuer}{OAUTH_LOGIN_PATH}?{urlencode({'request': login_request})}"

    async def handle_login(self, request: Request) -> Response:
        if request.method == "GET":
            login_request = request.query_params.get("request", "")
            if self._verify("login", login_request) is None:
                return self._login_error_page(OAUTH_INVALID_REQUEST_MESSAGE)
            return self._login_form(login_request)

        form = await request.form()
        login_request = str(form.get("request", ""))
        claims = self._verify("login", login_request)
        if claims is None:
            return self._login_error_page(OAUTH_INVALID_REQUEST_MESSAGE)

        passphrase = str(form.get("passphrase", "")).encode()
        if not hmac.compare_digest(passphrase, self._passphrase):
            await asyncio.sleep(OAUTH_FAILED_LOGIN_DELAY_SECONDS)
            return self._login_form(
                login_request, error=OAUTH_WRONG_PASSPHRASE_MESSAGE, status_code=401
            )

        code = self._sign(
            "code",
            {**self._pick(claims, OAUTH_LOGIN_CLAIMS), "jti": secrets.token_urlsafe(16)},
            OAUTH_AUTHORIZATION_CODE_TTL_SECONDS,
        )
        redirect_to = construct_redirect_uri(
            claims["redirect_uri"], code=code, state=claims["state"]
        )
        return RedirectResponse(redirect_to, status_code=302, headers=NO_STORE_HEADERS)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        claims = self._verify("code", authorization_code)
        if claims is None or claims["client_id"] != client.client_id:
            return None
        if claims["jti"] in self._used_codes:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=claims["scopes"],
            expires_at=claims["exp"],
            client_id=claims["client_id"],
            code_challenge=claims["code_challenge"],
            redirect_uri=claims["redirect_uri"],
            redirect_uri_provided_explicitly=claims["redirect_uri_explicit"],
            resource=claims.get("resource"),
            subject=OAUTH_TOKEN_SUBJECT,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        claims = self._verify("code", authorization_code.code)
        if claims is not None:
            self._mark_code_used(claims["jti"], claims["exp"])
        return self._issue_tokens(
            client.client_id, authorization_code.scopes, authorization_code.resource
        )

    def _mark_code_used(self, jti: str, expires_at: float) -> None:
        now = time.time()
        self._used_codes = {k: exp for k, exp in self._used_codes.items() if exp > now}
        self._used_codes[jti] = expires_at

    def _issue_tokens(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        claims = {
            "client_id": client_id,
            "scopes": scopes,
            "resource": resource,
            "sub": OAUTH_TOKEN_SUBJECT,
        }
        return OAuthToken(
            access_token=self._sign("access", claims, OAUTH_ACCESS_TOKEN_TTL_SECONDS),
            token_type="Bearer",
            expires_in=OAUTH_ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=self._sign("refresh", claims, OAUTH_REFRESH_TOKEN_TTL_SECONDS),
            scope=" ".join(scopes) or None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        claims = self._verify("refresh", refresh_token)
        if claims is None or claims["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=claims["client_id"],
            scopes=claims["scopes"],
            expires_at=claims["exp"],
            resource=claims.get("resource"),
            subject=claims["sub"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        return self._issue_tokens(
            client.client_id, scopes or refresh_token.scopes, refresh_token.resource
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        claims = self._verify("access", token)
        if claims is None:
            return None
        return AccessToken(
            token=token,
            client_id=claims["client_id"],
            scopes=claims["scopes"],
            expires_at=claims["exp"],
            resource=claims.get("resource"),
            subject=claims["sub"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # ステートレスのため個別失効はできない（失効エンドポイントは無効化している）
        return None

    @staticmethod
    def is_allowed_redirect_uri(uri: str) -> bool:
        if uri == CLAUDE_CALLBACK_URL:
            return True
        parsed = urlparse(uri)
        return parsed.scheme == "http" and parsed.hostname in OAUTH_LOOPBACK_HOSTS

    @staticmethod
    def _pick(claims: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return {key: claims.get(key) for key in keys}

    @staticmethod
    def _page(body: str, status_code: int = 200) -> HTMLResponse:
        document = Template(LOGIN_PAGE_TEMPLATE).substitute(body=body)
        return HTMLResponse(document, status_code=status_code, headers=LOGIN_PAGE_HEADERS)

    @staticmethod
    def _error_html(message: str) -> str:
        return Template(LOGIN_ERROR_TEMPLATE).substitute(message=html.escape(message))

    @staticmethod
    def _login_form(
        login_request: str, error: str | None = None, status_code: int = 200
    ) -> HTMLResponse:
        body = Template(LOGIN_FORM_TEMPLATE).substitute(
            error_html=ClaudeOAuthProvider._error_html(error) if error else "",
            action=html.escape(OAUTH_LOGIN_PATH),
            login_request=html.escape(login_request),
        )
        return ClaudeOAuthProvider._page(body, status_code=status_code)

    @staticmethod
    def _login_error_page(message: str) -> HTMLResponse:
        return ClaudeOAuthProvider._page(ClaudeOAuthProvider._error_html(message), status_code=400)
