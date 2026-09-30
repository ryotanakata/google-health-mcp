---
paths:
  - "src/**/*.py"
  - "auth_setup.py"
  - "Dockerfile"
---

# アーキテクチャ規約

`README.md` の「MCP Tools」と「Data Constraints」（`README.ja.md` では「MCP ツール」「データ取得の制約」）に反する実装をしない。

## システム構成

| 構成要素 | 役割 | 置き場所 |
|---|---|---|
| Claude（claude.ai / Desktop / mobile / Claude Code） | MCP クライアント。OAuth で接続し、ツールを呼ぶ | — |
| MCP サーバー（本リポジトリ） | Claude 向けの OAuth 認可サーバー兼 MCP サーバー。Google Health API を呼んで要約を返す | Cloud Run |
| Google OAuth | Google Health API 用の access_token を refresh_token から発行する | Google |
| Google Health API v4 | Google Fitbit Air（主な対象）と Fitbit / Pixel Watch の健康データ | Google |
| Secret Manager | `MCP_SHARED_SECRET`・`MCP_TOKEN_SIGNING_KEY`・`GOOGLE_CLIENT_SECRET`・`GOOGLE_REFRESH_TOKEN` | GCP |

## システムの不変条件

- **利用者は本人1人**。複数ユーザー・テナント分離の仕組みを持ち込まない
- **サーバーは状態を持たない**。DB・ファイル・インスタンスのメモリに、再起動後も必要な状態を保存しない（Cloud Run はスケールゼロと複数インスタンスがある）。必要な状態は署名付きトークンに載せる
- **Google の refresh_token はローカルでのみ取得する**（`auth_setup.py`）。ブラウザの同意画面が必要なため、サーバー上で取得する経路を作らない。同意画面が「テスト」のままだと7日で失効するので、`scripts/refresh_google_token.sh` で取り直す
- **生の時系列データを Claude に返さない**。MCP サーバー側で要約してから返す（コンテキストトークン節約のため）
- **サーバーはリクエストの処理中以外に CPU を使わない**。サーバーに常駐する接続・バックグラウンド処理・定期実行を入れない（利用者の PC で動く `scripts/` の定期実行は対象外。Cloud Run の「CPU はリクエスト処理中のみ割り当て」で無料枠内に収めるため）
- **読み取り専用**。Google Health API への書き込み（`create` / `patch` / `batchDelete`）を実装しない

## 処理の流れ

- **Google の認可（ローカル）**: `auth_setup.py` → Google 同意画面 → refresh_token を Secret Manager に登録。同意画面が「テスト」の間は `scripts/refresh_google_token.sh` で5日ごとに取り直す
- **コネクタ接続（Claude 側で1回）**: Claude が `/register` で動的クライアント登録 → `/authorize` → 同意画面（`/oauth/login`）でパスフレーズ入力 → `/token` でトークン取得
- **ツール呼び出し（定常）**: Claude → `/mcp`（OAuth トークン検証）→ `service`（入力検証）→ `repository`（Google Health API 呼び出し。access_token が期限切れなら `google_auth` が refresh_token で再発行）→ `service`（要約）→ Claude
- refresh_token 自体が失効していた場合は `RefreshTokenExpiredError` を返し、`auth_setup.py` の再実行（`scripts/refresh_google_token.sh`）を利用者に促す（サーバーは自動復旧しない）

## モジュールの責務

| モジュール | 責務 | 置かないもの |
|---|---|---|
| `main.py` | MCPサーバーの組み立て、`@mcp.tool()` の定義、ASGIアプリ生成 | 要約ロジック、HTTP呼び出し |
| `service.py` | ツール入力の検証（日付形式・期間の前後・期間の上限）と、生データの要約 | HTTP呼び出し、APIのパス・filter文字列 |
| `repository.py` | Google Health API の呼び出し、ページング、クエリ期間上限による分割・結合 | 要約・整形 |
| `google_auth.py` | Google の refresh_token から access_token を取得・更新（Googleに対するOAuthクライアント側） | Claude向けの認証 |
| `claude_auth.py` | Claude向けOAuth 2.1認可サーバー（同意画面・トークン発行） | Google向けの認証 |
| `config.py` | 環境変数の読み込みと検証 | 定数 |
| `constants.py` | 全モジュールの定数（`constants.md` 参照） | 処理 |
| `models.py` | ツール戻り値の型（`mcp-tools.md` 参照） | 処理 |

- API の都合（エンドポイント、リクエスト形式、ページング、90日分割）は `repository.py` で吸収し、`service.py` に漏らさない
- `service.py` の要約処理は副作用を持たない（通信・時刻取得・乱数を使わない）

## 依存の向き

- 依存は `main → service → repository → google_auth` の一方向にする。逆向きの import は禁止
- `claude_auth` は `main` からのみ使う
- `config` / `constants` / `models` はどこから import してもよい（これらは他の `src` モジュールを import しない。ただし `config` は検証に使う上限値のため `constants` を import してよい）

## クラスと関数

- クラスを持つモジュールでは、`self` を使わない処理もモジュール直下の関数にせず、そのクラスの `@staticmethod` にする
  - インスタンスメソッドからは `self.xxx(...)`、staticmethod 同士はクラス名で `HealthService._first(...)` と呼ぶ
- 例外（モジュール直下の関数でよい）: `main.py` の `@mcp.tool()` 関数と `create_app`、`auth_setup.py` の `main`、テストの補助関数
- 例外（`self` を使わなくてもインスタンスメソッドのままにする）: MCP SDK のインターフェース（`OAuthAuthorizationServerProvider` など）が定めるメソッド。`ClaudeOAuthProvider.revoke_token` など

```python
# NG: クラスを持つモジュールの直下に関数を置く
class HealthService: ...

def summarize_sleep(date: str, points: list[dict]) -> SleepLog: ...

# OK
class HealthService:
    @staticmethod
    def summarize_sleep(date: str, points: list[dict]) -> SleepLog: ...
```

## 命名

- 認証まわりのモジュール・クラスは相手の名前を付けて区別する（`google_auth` / `claude_auth`、`GoogleAuthManager` / `ClaudeOAuthProvider`）。`auth.py` / `oauth.py` のような区別できない名前にしない
- 標準ライブラリと衝突するモジュール名（`types.py` など）を使わない

## 運用上の制約

- Cloud Run への実デプロイ・GCP リソースの作成変更は、人間が明示的に指示したときのみ行う。通常のデプロイは `main` へのマージ後に `.github/workflows/deploy.yml` が行う（GCP への認証は Workload Identity Federation で、このリポジトリの `main` の `production` 環境に限る）
- 新しいツールを追加するときは、必要な Google のスコープが `GOOGLE_HEALTH_SCOPES` に含まれているか確認する。スコープを増やすと refresh_token の取り直し（`auth_setup.py` の再実行）が必要になる
