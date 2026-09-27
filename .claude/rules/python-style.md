---
paths:
  - "**/*.py"
---

# Python スタイル規約

- 対象バージョンは Python 3.11 以上（`pyproject.toml` の `requires-python`）。本番の Dockerfile は 3.12
- 空の `__init__.py` を除くすべてのモジュールの先頭（docstring の直後）に `from __future__ import annotations` を書く
- lint は `ruff check .`（`pyproject.toml` の設定: 行長100、`E`/`F`/`I`/`UP`/`B`）を通す。行長は日本語のコメント・docstring も含めて100以内にする
- コメント・docstring は日本語で書く。何を書くか・書かないかは `comment.md` に従う
- `src/` と `auth_setup.py` の関数の引数・戻り値には型注釈を付ける（テスト関数・fixture は対象外）
- 型注釈は `X | None`、`list[dict]` などの組み込み構文を使う（`Optional` / `List` を使わない）
- 外部要因・設定のエラーは用途ごとに `RuntimeError` を継承した専用クラスを定義し（`HealthApiError`、`RefreshTokenExpiredError`、`ConfigError`）、原因は `raise ... from exc` でつなぐ
- ツール入力の検証エラー（日付の形式、期間の前後）は標準の `ValueError` を送出する（MCP SDK がメッセージを Claude に返す）
