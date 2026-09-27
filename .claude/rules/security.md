---
paths:
  - "src/**/*.py"
  - "auth_setup.py"
  - "Dockerfile"
  - ".env.example"
---

# セキュリティ規約

扱うのは個人の健康データ。トークン1本の漏洩が全データの漏洩になる前提で守る。

## 秘密情報

- `MCP_SHARED_SECRET`・`GOOGLE_CLIENT_SECRET`・`GOOGLE_REFRESH_TOKEN`・アクセストークン・パスフレーズをログ・例外メッセージ・レスポンスに含めない
- 例外: `auth_setup.py` が取得した refresh_token を標準出力に表示するのは意図した動作（利用者が Secret Manager に登録するため）。サーバーのコードでは表示しない
- 秘密情報をコード・テストの fixture 以外に直書きしない。`.env` をコミットしない（`.env.example` には空欄かダミー値のみ）
- 秘密情報やトークンを URL のクエリ文字列で受け取らない・渡さない
  - 例外: OAuth の標準フローでクエリに載る中間値（認可コード `code`、`state`、同意画面に渡す署名済みの `request`）。これらは単体では資格情報として使えない（認可コードは PKCE の code_verifier がないと交換できない）
  - アクセストークン・リフレッシュトークンは `/token` のレスポンス本文でのみ返す
- 秘密情報の比較には `hmac.compare_digest` を使う。`==` / `!=` で比較しない

```python
# NG
if passphrase != self._passphrase: ...

# OK
if not hmac.compare_digest(passphrase, self._passphrase): ...
```

## Claude向けOAuth（`claude_auth.py`）

- クライアント登録で受け付ける redirect_uri は `CLAUDE_CALLBACK_URL` と http のループバック（`OAUTH_LOOPBACK_HOSTS`）のみ。許可リストを広げる変更は人間の確認なしに行わない
- 認可コード・トークン・クライアント情報は署名付きJWTで発行し、検証では `typ`（用途）と `iss` を必ず確認する。用途の違うトークンを受け入れない（例: refresh トークンをアクセストークンとして通さない）
- パスフレーズ誤りには `OAUTH_FAILED_LOGIN_DELAY_SECONDS` の待ちを入れる
- 同意画面の HTML に差し込む値はすべて `html.escape` する。エスケープせずに差し込むのは禁止
- 同意画面のレスポンスには `LOGIN_PAGE_HEADERS`（`Cache-Control: no-store`・`X-Frame-Options: DENY`）を付ける

## MCP エンドポイント（`main.py`）

- `TransportSecuritySettings` の DNS リバインディング対策を無効にしない。許可ホストは `PUBLIC_BASE_URL` のホストに限定する
- `/mcp` は必ず OAuth で保護する（`auth_server_provider` と `AuthSettings` を外さない）

## Google 側の認証（`google_auth.py`）

- refresh_token の失効（`google.auth.exceptions.RefreshError`）は `RefreshTokenExpiredError` にラップし、`auth_setup.py` の再実行が必要な旨をメッセージで明示する
- 要求するスコープは読み取り専用（`.readonly`）のみ。書き込みスコープを追加しない
