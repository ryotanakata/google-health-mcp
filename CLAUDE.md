# CLAUDE.md

Google Fitbit Air（主な対象。Fitbit / Pixel Watch も同じ API で扱える）の健康データを Google Health API v4 経由で Claude.ai から呼び出すリモート MCP サーバー（Python・Streamable HTTP・Cloud Run）。ツール仕様とデータ取得の制約は `README.md`（日本語版は `README.ja.md`）が正。

## コマンド

```bash
pip install -r requirements-dev.txt   # 開発依存を含めてインストール
ruff check .                           # lint
pytest                                 # テスト
python -m src.main                     # ローカルでサーバー起動（要 .env）
python auth_setup.py --client-secret client_secret.json  # 初回のみ・ローカルでrefresh_token取得
```

## 構成

```
src/
├── main.py          # MCPサーバーの組み立て・ツール定義（/mcp）
├── service.py       # 入力検証と要約
├── repository.py    # Google Health API呼び出し・ページング・90日分割
├── google_auth.py   # Google refresh_tokenの自動更新
├── claude_auth.py   # Claude向けOAuth 2.1認可サーバー
├── config.py        # 環境変数
├── constants.py     # 全モジュールの定数・HTMLテンプレート
└── models.py        # ツール戻り値のTypedDict
```

## 規約

コーディング規約は `.claude/rules/` にある。作業対象のファイルに対応する規約を読んでから実装する（コミット前の規約レビューもこれを照合する）。

| ファイル | 内容 |
|---|---|
| `architecture.md` | システム構成・不変条件・処理の流れ・モジュールの責務・依存の向き・`@staticmethod`・命名 |
| `constants.md` | 定数は `constants.py` に集約・接頭辞・HTMLテンプレート |
| `security.md` | 秘密情報・OAuth・エスケープ・Host検証 |
| `google-health-api.md` | エンドポイント・filter・クエリ期間上限・proto3 JSON の扱い |
| `mcp-tools.md` | MCP SDK 2.x・ツール定義・戻り値の型 |
| `testing.md` | respx・FakeRepository・OAuthフローのテスト |
| `comment.md` | コメント・docstring・ドキュメントに書くもの／書かないもの |
| `python-style.md` | Python バージョン・lint・型注釈・例外 |

## 現状の注意点

- Google Health API は公式ディスカバリドキュメントに沿って実装済みだが、実アカウントでの疎通確認はまだ
- Cloud Run の URL はデプロイ後に決まるため、`PUBLIC_BASE_URL` を設定して再デプロイが必要

## 自律開発ループ（Notion連携）

Notion起票（背景・要求・受け入れ条件AC） → ルーチン実行（実装・自己レビュー・PR作成） → 人間レビュー → マージ

- 設定: `.claude/notion.json`（DB ID・Status値・baseブランチ・テストコマンド。値をコードに直書きしない）
- ルーチンのプロンプト: `.claude/routine-prompt.md`（クラウド側ルーチンの設定にそのまま使う）
- コミット前の規約レビュー: `rules-review` スキル。コミットは `.claude/hooks/review-gate.sh` のゲートを通過しないとブロックされる
- 出荷（レビュー→意味単位コミット→PR作成）: `ship` スキル
- PRテンプレート: `.github/pull_request_template.md`。`## 受け入れ条件（AC）` の見出しは表記を変えない
- ルーチンが作った PR は、人間がマージするまで Notion のタスクを完了にしない
- **ルーチンは Cloud Run への実デプロイ・GCPリソースの作成変更を行わない**（人間が明示的に指示したときのみ）
