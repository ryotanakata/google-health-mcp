---
paths:
  - "src/**/*.py"
  - "auth_setup.py"
---

# 定数規約

## 置き場所

- 定数は1つのモジュールでしか使わないものも含めて、すべて `src/constants.py` に置く。`constants.py` 以外のモジュールで大文字名の定数を定義しない
- 対象は URL・パス・スコープ・上限値・有効期限・待ち時間・HTTPヘッダー・画面の文言・HTMLテンプレートなど。処理の中に直書きしない
- `constants.py` の中は、使うモジュールごとに `# --- <用途>（<モジュール名>） ---` の見出しコメントで区切る
- 値の意味や根拠（APIの仕様、README の該当箇所）が名前から分からないものには、直前にコメントを付ける
- 例外: Google Health API のデータ型名（`"steps"`・`"sleep"` など）と filter 式（`sleep.interval.civil_end_time >= "..."`）は、リクエストそのものの記述として `repository.py` のメソッド内に書く（SQL をクエリの呼び出し箇所に書くのと同じ扱い。定数に分けると filter とデータ型名の対応が読めなくなるため）

```python
# NG: モジュール内で定数を定義する / 処理に直書きする
# src/repository.py
SESSION_PAGE_SIZE = 25
raw = await self._request("GET", "/sleep/dataPoints", params={"pageSize": 25})

# OK
# src/constants.py
# exercise・sleep の list は pageSize の上限が25
SESSION_PAGE_SIZE = 25
```

## 命名

- 1か所に集めても用途が分かるよう、用途の接頭辞を付ける（`OAUTH_` / `GOOGLE_` / `HEALTH_API_` など）
- 特に Google 側と Claude 側で同じ概念（スコープ、トークン、有効期限）は、必ず接頭辞で区別する

```python
# NG: どちらのスコープか分からない
SCOPE = "health"
ACCESS_TOKEN_TTL_SECONDS = 3600

# OK
OAUTH_SCOPE = "health"
OAUTH_ACCESS_TOKEN_TTL_SECONDS = 60 * 60
```

## HTMLテンプレート

- HTML は `constants.py` に `string.Template` 形式（`$変数`）の文字列として置く。f-string や `str.format` 形式にしない（CSS の `{ }` をエスケープせずに書くため）
- 展開は `Template(...).substitute(...)` を使う（`safe_substitute` は使わない。変数名のずれを例外で検出するため）
- 差し込む値は、展開する側（`claude_auth.py`）で必ず `html.escape` してから渡す（`security.md` 参照）
