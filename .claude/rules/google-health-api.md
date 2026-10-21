---
paths:
  - "src/repository.py"
  - "src/service.py"
  - "src/constants.py"
---

# Google Health API 規約

## 仕様の正

- エンドポイント・リクエスト・レスポンスの形は公式ディスカバリドキュメント（`https://health.googleapis.com/$discovery/rest?version=v4`）に合わせる。Fitbit Web API v1 の形（`/activities/date/{date}` 等）を使わない
- 日次の集計値は `POST users/me/dataTypes/{type}/dataPoints:dailyRollUp`、セッション・生データは `GET users/me/dataTypes/{type}/dataPoints` を使う
- `filter` は AIP-160 形式。データ型名はパスではハイフン（`daily-resting-heart-rate`）、filter ではアンダースコア（`daily_resting_heart_rate.date`）で書く
- sleep は `sleep.interval.civil_end_time`、それ以外のセッション（exercise・nutrition-log・hydration-log）は `{session_data_type}.interval.civil_start_time` で期間を絞る（sleep は開始時刻での絞り込みに対応していない）

## 書き込み（食事・水分のみ）

- 記録は `POST users/me/dataTypes/{type}/dataPoints`（`create`）、削除は `dataPoints:batchDelete`。書き込めるデータ型は `nutrition-log` と `hydration-log` だけ（`architecture.md` の不変条件）
- `create` は1件ずつしか受け付けない。複数品目は品目の数だけ呼ぶ
- `create` / `batchDelete` は `Operation` を返す。作成されたデータポイントは `Operation.response` に入る。HTTP が 200 でも `Operation.error` に失敗が入ることがあり、`done` が false のうちは `response` が返らない
- `interval`（`SessionTimeInterval`）には `startTime`・`endTime`（UTC）と `startUtcOffset`・`endUtcOffset`（`"32400s"` 形式の Duration）が必須で、`startTime < endTime` でなければならない
- 食事は匿名方式（`foodDisplayName` と栄養素を直接指定）で記録する。食品 DB を名前で検索する filter が無いため。**匿名方式の記録は編集できない**ので、直すときは削除して作り直す
- エネルギーは `energy` の `kcal`、たんぱく質は `nutrients` の `PROTEIN`（`quantity.grams`）、脂質は `totalFat`、炭水化物は `totalCarbohydrate`（いずれも `grams`）に入れる。水分は `amountConsumed` の `milliliters`
- `batchDelete` の `parent` は `names` の親と一致している必要があるため、データ型ごとに分けて送る
- 403 はスコープ不足として `InsufficientScopeError` にし、refresh_token の取り直しが必要な旨をメッセージで明示する（スコープは refresh_token に固定されるため、増やしたら取り直す）

## クエリ制約（README の Data Constraints）

- 1リクエストの期間は `QUERY_MAX_DAYS`（90日）以下にする。heart-rate / active-minutes / total-calories / calories-in-heart-rate-zone の複数日集計は14日以下にする（現在の集計は1日単位のみ。複数日集計を追加するときは14日の上限を `constants.py` に定数として追加する）
- 上限を超えうる期間は `HealthRepository.split_date_range` で分割して取得・結合する
- list は `nextPageToken` がなくなるまでページングする。1ページ目だけで打ち切らない
- exercise・sleep の `pageSize` は `SESSION_PAGE_SIZE`（25）以下にする

## レスポンスの扱い（proto3 JSON）

- int64 は文字列で返る（`"countSum": "8000"`）。数値として使う前に `int()` で変換する
- 変換が必要かはディスカバリドキュメントのフィールド定義で判断する。`"type": "string", "format": "int64"` は `int()` で変換し、`"type": "number", "format": "double"` は数値のまま扱う
  - int64（文字列）: `countSum`・`millimetersSum`・`activeMinutesSum`・`averageHeartRateBeatsPerMinute`・`beatsPerMinute`・`minutesAsleep`
  - double（数値）: `kcalSum`・`caloriesKcal`・`distanceMillimeters`
- 0・空の値はフィールドごと省略される。`raw["x"]` で直接参照せず `.get("x", 既定値)` で読む
- Duration は `"3600s"` 形式の文字列で返る

```python
# NG: 省略されたフィールドで KeyError、文字列のまま返す
steps = raw["steps"]["countSum"]

# OK
steps = HealthService._to_int(raw.get("steps", {}).get("countSum"))
```

## 要約（`service.py`）

- 生の時系列データをそのままツールの戻り値にしない。必ず要約してから返す（Claude のコンテキスト節約のため）
- API が返さない指標（睡眠効率など）を算出する場合は、算出式をコメントに残す
- 「睡眠スコア」「Daily Readiness Score」など Google 独自のスコアは API で取得できない。必要なら自前で算出し、独自算出である旨をコメントに残す
