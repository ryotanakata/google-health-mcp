---
paths:
  - "tests/**/*.py"
  - "src/**/*.py"
---

# テスト規約

## 前提

- テスト環境に実際の Google 認証情報・GCP リソースは存在しない。実 API・実 OAuth に接続するテストを書かない
- `src/` の変更には、対応するテストの追加・更新を含める。テストファイルは処理を持つモジュールごとに `tests/test_<モジュール名>.py` とする（`constants.py`・`models.py` は対象外）

## モジュール別の書き方

| 対象 | 書き方 |
|---|---|
| `repository.py` | `httpx` の呼び出しを `respx` でモックし、リクエストのパス・body・filter・ページングを検証する |
| `service.py` | `FakeRepository` を渡してテストする。要約の staticmethod は生データを直接渡して検証する（HTTP モック不要） |
| `google_auth.py` | `Credentials.valid` / `refresh` を `unittest.mock.patch` で差し替える |
| `claude_auth.py` | HTMLエスケープ・redirect_uri の許可判定を単体で検証する |
| `main.py` | ASGI アプリに `httpx.ASGITransport` で接続し、OAuth のフロー全体（登録→認可→トークン→`/mcp`）を通す |

- `main.py` のテストでは、lifespan の開始と終了を同じタスク内で行う（`_serve` のような async context manager を使う）。async fixture で lifespan を起動しない（anyio の cancel scope エラーになる）
- `src.main` はインポート時に環境変数を読むため、テストでは環境変数を設定してから `sys.modules` を外して再インポートする

## セキュリティのテスト

- 認証・認可・エスケープを変更したときは、拒否される側のケース（誤ったパスフレーズ、許可外の redirect_uri、不正なトークン、許可外の Host、悪意ある文字列）を必ずテストする
- 待ち時間（`OAUTH_FAILED_LOGIN_DELAY_SECONDS`）は `monkeypatch` で 0 にしてテストを遅くしない
