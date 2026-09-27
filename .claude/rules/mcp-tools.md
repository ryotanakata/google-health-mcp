---
paths:
  - "src/main.py"
  - "src/models.py"
  - "src/service.py"
---

# MCP ツール規約

## SDK

- MCP SDK は 2.x（`mcp>=2.0.0,<3.0.0`）。サーバーは `mcp.server.mcpserver.MCPServer` を使う。1.x の `FastMCP`（`mcp.server.fastmcp`）を使わない
- トランスポートは Streamable HTTP（`/mcp`）を `stateless_http=True`・`json_response=True` で使う。SSE（`sse_app`）を使わない（MCPで非推奨。Cloud Run の複数インスタンス・スケールゼロと相性が悪く、接続を張り続けてCPU課金も増える）

## ツールの定義

- ツール関数は `main.py` に `@mcp.tool()` で定義し、中身は `HealthService` のメソッドを呼ぶだけにする
- ツールの docstring に、引数の形式（`YYYY-MM-DD`、期間の両端を含むか）と返す内容を書く（Claude がツールを選ぶ手がかりになる）
- 追加・変更するツールは `README.md` の「MCP Tools」と一致させる。ツールを変えたら `README.md` と `README.ja.md` を同じ変更で更新する

## 戻り値の型

- ツールの戻り値は `dict` にせず、`src/models.py` の TypedDict で型注釈する（MCP SDK がここから outputSchema を生成し、Claude に構造が伝わる）
- TypedDict は `typing_extensions` から import する（pydantic が Python 3.12 未満で `typing.TypedDict` を受け付けないため）
- 型を付けるのはツールの戻り値のみ。Google Health API の生レスポンスは型付けせず `dict` で扱う
- 型定義のモジュール名に `types` を使わない（標準ライブラリと衝突する）

```python
# NG
@mcp.tool()
async def get_sleep_log(date: str) -> dict: ...

# OK
@mcp.tool()
async def get_sleep_log(date: str) -> SleepLog: ...
```
