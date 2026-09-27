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
- sleep は `sleep.interval.civil_end_time`、exercise は `exercise.interval.civil_start_time` で期間を絞る（sleep は開始時刻での絞り込みに対応していない）

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
