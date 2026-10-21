"""リクエスト・レスポンスの形は公式ディスカバリドキュメントに準拠する。

https://health.googleapis.com/$discovery/rest?version=v4
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from collections.abc import AsyncIterator

import httpx

from src.constants import (
    HEALTH_API_BASE_URL,
    HEALTH_API_MAX_ERROR_BODY_CHARS,
    HEALTH_API_TIMEOUT_SECONDS,
    NUTRITION_WRITABLE_DATA_TYPES,
    QUERY_MAX_DAYS,
    QUERY_SHORT_MAX_DAYS,
    SESSION_PAGE_SIZE,
)
from src.google_auth import GoogleAuthManager


class HealthApiError(RuntimeError):
    pass


class InsufficientScopeError(HealthApiError):
    pass


class HealthRepository:
    def __init__(
        self, auth_manager: GoogleAuthManager, base_url: str = HEALTH_API_BASE_URL
    ) -> None:
        self._auth_manager = auth_manager
        self._base_url = base_url

    async def fetch_activity_rollups(self, day: dt.date) -> dict[str, list[dict]]:
        data_types = (
            "steps",
            "distance",
            "total-calories",
            "active-minutes",
            "floors",
            "active-zone-minutes",
        )
        async with self._client() as client:
            results = await asyncio.gather(
                *(self._daily_rollup(client, data_type, day, day) for data_type in data_types)
            )
        return dict(zip(data_types, results, strict=True))

    async def fetch_daily_metrics(self, day: dt.date) -> dict[str, list[dict]]:
        """day の日次サマリー（HRV・呼吸数・SpO2・皮膚温・VO2 Max）をデータ型ごとに返す。"""
        data_types = (
            "daily-heart-rate-variability",
            "daily-respiratory-rate",
            "daily-oxygen-saturation",
            "daily-sleep-temperature-derivations",
            "daily-vo2-max",
        )
        next_day = day + dt.timedelta(days=1)
        async with self._client() as client:
            results = await asyncio.gather(
                *(
                    self._list(client, data_type, self._daily_date_filter(data_type, day, next_day))
                    for data_type in data_types
                )
            )
        return dict(zip(data_types, results, strict=True))

    async def fetch_body_measurements(self, start: dt.date, end: dt.date) -> dict[str, list[dict]]:
        """start・end の両日を含む期間の体重・体脂肪率をデータ型ごとに返す。"""
        data_types = ("weight", "body-fat")
        async with self._client() as client:
            results = await asyncio.gather(
                *(
                    self._sample_time_list(client, data_type, start, end)
                    for data_type in data_types
                )
            )
        return dict(zip(data_types, results, strict=True))

    async def fetch_activity_trend(
        self, start: dt.date, end: dt.date
    ) -> tuple[dict[str, list[dict]], list[dict]]:
        """(データ型ごとの日次集計, 安静時心拍数) を返す。start・end の両日を含む。"""
        rollups = (
            ("steps", QUERY_MAX_DAYS),
            ("distance", QUERY_MAX_DAYS),
            ("total-calories", QUERY_SHORT_MAX_DAYS),
            ("active-minutes", QUERY_SHORT_MAX_DAYS),
            ("floors", QUERY_MAX_DAYS),
            ("active-zone-minutes", QUERY_MAX_DAYS),
        )
        async with self._client() as client:
            *rollup_points, resting = await asyncio.gather(
                *(
                    self._split_daily_rollup(client, data_type, start, end, max_days)
                    for data_type, max_days in rollups
                ),
                self._daily_date_list(client, "daily-resting-heart-rate", start, end),
            )
        return dict(
            zip((data_type for data_type, _ in rollups), rollup_points, strict=True)
        ), resting

    async def fetch_sleep_sessions(self, day: dt.date) -> list[dict]:
        """day に終わった睡眠を返す（前夜〜当日朝の睡眠と当日の仮眠）。

        sleep は開始時刻での絞り込みに対応していないため、終了時刻で絞る。
        """
        next_day = day + dt.timedelta(days=1)
        async with self._client() as client:
            return await self._list(
                client,
                "sleep",
                f'sleep.interval.civil_end_time >= "{day.isoformat()}" '
                f'AND sleep.interval.civil_end_time < "{next_day.isoformat()}"',
                page_size=SESSION_PAGE_SIZE,
            )

    async def fetch_heart_rate(self, day: dt.date) -> tuple[list[dict], list[dict]]:
        """(安静時心拍数のデータポイント, 心拍ゾーン滞在時間の日次集計) を返す。"""
        next_day = day + dt.timedelta(days=1)
        async with self._client() as client:
            resting, zones = await asyncio.gather(
                self._list(
                    client,
                    "daily-resting-heart-rate",
                    self._daily_date_filter("daily-resting-heart-rate", day, next_day),
                ),
                self._daily_rollup(client, "time-in-heart-rate-zone", day, day),
            )
        return resting, zones

    async def fetch_exercises(self, start: dt.date, end: dt.date) -> list[dict]:
        """start・end の両日を含む。"""
        points: list[dict] = []
        async with self._client() as client:
            for chunk_start, chunk_end in self.split_date_range(start, end, QUERY_MAX_DAYS):
                exclusive_end = chunk_end + dt.timedelta(days=1)
                points.extend(
                    await self._list(
                        client,
                        "exercise",
                        f'exercise.interval.civil_start_time >= "{chunk_start.isoformat()}" '
                        f'AND exercise.interval.civil_start_time < "{exclusive_end.isoformat()}"',
                        page_size=SESSION_PAGE_SIZE,
                    )
                )
        return points

    async def fetch_nutrition_log(self, day: dt.date) -> dict[str, list[dict]]:
        """day の食事・水分の記録（list）と、その日の合計（dailyRollUp）を返す。"""
        next_day = day + dt.timedelta(days=1)
        async with self._client() as client:
            meals, hydration, meal_totals, hydration_totals = await asyncio.gather(
                self._list(
                    client,
                    "nutrition-log",
                    self._session_date_filter("nutrition-log", day, next_day),
                ),
                self._list(
                    client,
                    "hydration-log",
                    self._session_date_filter("hydration-log", day, next_day),
                ),
                self._daily_rollup(client, "nutrition-log", day, day),
                self._daily_rollup(client, "hydration-log", day, day),
            )
        return {
            "nutrition-log": meals,
            "hydration-log": hydration,
            "nutrition-log-rollup": meal_totals,
            "hydration-log-rollup": hydration_totals,
        }

    async def fetch_paired_devices(self) -> list[dict]:
        devices: list[dict] = []
        params: dict = {}
        async with self._client() as client:
            while True:
                raw = await self._request(client, "GET", "/pairedDevices", params=params)
                devices.extend(raw.get("pairedDevices", []))
                next_page_token = raw.get("nextPageToken")
                if not next_page_token:
                    return devices
                params = {**params, "pageToken": next_page_token}

    async def create_nutrition_logs(
        self, meal_type: str, items: list[dict], start: dt.datetime, end: dt.datetime
    ) -> list[dict]:
        """品目ごとに1件ずつ作成し、作成されたデータポイントを items と同じ順で返す。

        create は1件ずつしか受け付けないため、品目の数だけ直列に送る。
        """
        interval = self._session_interval(start, end)
        created: list[dict] = []
        async with self._client() as client:
            for item in items:
                operation = await self._request(
                    client,
                    "POST",
                    "/dataTypes/nutrition-log/dataPoints",
                    json={"nutritionLog": self._nutrition_log(item, meal_type, interval)},
                )
                created.append(self._operation_response(operation))
        return created

    async def create_hydration_log(
        self, amount_ml: float, start: dt.datetime, end: dt.datetime
    ) -> dict:
        async with self._client() as client:
            operation = await self._request(
                client,
                "POST",
                "/dataTypes/hydration-log/dataPoints",
                json={
                    "hydrationLog": {
                        "interval": self._session_interval(start, end),
                        "amountConsumed": {"milliliters": amount_ml},
                    }
                },
            )
        return self._operation_response(operation)

    async def delete_nutrition_data_points(self, names: list[str]) -> None:
        """batchDelete は parent と name の親が一致している必要があるため、データ型ごとに送る。"""
        by_data_type: dict[str, list[str]] = {}
        for name in names:
            data_type = self.data_point_data_type(name)
            # 書き込めるデータ型以外へパスを組み立てないための歯止め（architecture.md の不変条件）。
            # 利用者に返す入力エラーは service が先に出す
            if data_type not in NUTRITION_WRITABLE_DATA_TYPES:
                raise HealthApiError(f"削除できないデータ型のnameです: {name!r}")
            by_data_type.setdefault(data_type, []).append(name)
        async with self._client() as client:
            for data_type, data_point_names in by_data_type.items():
                operation = await self._request(
                    client,
                    "POST",
                    f"/dataTypes/{data_type}/dataPoints:batchDelete",
                    json={"names": data_point_names},
                )
                self._operation_response(operation)

    @staticmethod
    def data_point_data_type(name: str) -> str | None:
        """name の形式は users/{user}/dataTypes/{data_type}/dataPoints/{data_point}。"""
        parts = name.split("/")
        if len(parts) != 6 or parts[0] != "users" or parts[2] != "dataTypes":
            return None
        if parts[4] != "dataPoints" or not parts[5]:
            return None
        return parts[3]

    @contextlib.asynccontextmanager
    async def _client(self) -> AsyncIterator[httpx.AsyncClient]:
        """fetch_* 1回分の並列・ページング・分割リクエストで接続を共有するクライアント。

        接続を常駐させないよう、fetch_* を抜けたら閉じる。
        """
        token = await self._auth_manager.get_access_token()
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=HEALTH_API_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            yield client

    @staticmethod
    async def _request(
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        response = await client.request(method, path, params=params, json=json)
        body = response.text[:HEALTH_API_MAX_ERROR_BODY_CHARS]
        # access_token は毎回 refresh_token から発行し直すため、403 は権限不足。付与済みの
        # スコープは refresh_token に固定されるので、スコープを増やしたあとは取り直しが要る
        if response.status_code == httpx.codes.FORBIDDEN:
            raise InsufficientScopeError(
                "Google Health APIへのアクセスが拒否されました（403）。refresh_tokenに必要な"
                "スコープが付いていない可能性があります。OAuth同意画面にスコープを追加し、"
                f"auth_setup.pyを再実行してrefresh_tokenを取り直してください: {body}"
            )
        if response.status_code >= 400:
            raise HealthApiError(
                f"Google Health API呼び出しに失敗しました: {response.status_code} {body}"
            )
        return response.json()

    @staticmethod
    async def _daily_rollup(
        client: httpx.AsyncClient, data_type: str, start: dt.date, end: dt.date
    ) -> list[dict]:
        """start・end の両日を含む期間を1日ごとの枠で集計する。枠は civilStartTime で区別する。"""
        body: dict = {
            "range": {
                "start": HealthRepository._civil_date(start),
                "end": HealthRepository._civil_date(end + dt.timedelta(days=1)),
            },
            "windowSizeDays": 1,
        }
        points: list[dict] = []
        while True:
            raw = await HealthRepository._request(
                client, "POST", f"/dataTypes/{data_type}/dataPoints:dailyRollUp", json=body
            )
            points.extend(raw.get("rollupDataPoints", []))
            next_page_token = raw.get("nextPageToken")
            if not next_page_token:
                return points
            body = {**body, "pageToken": next_page_token}

    @staticmethod
    async def _split_daily_rollup(
        client: httpx.AsyncClient, data_type: str, start: dt.date, end: dt.date, max_days: int
    ) -> list[dict]:
        points: list[dict] = []
        for chunk_start, chunk_end in HealthRepository.split_date_range(start, end, max_days):
            points.extend(
                await HealthRepository._daily_rollup(client, data_type, chunk_start, chunk_end)
            )
        return points

    @staticmethod
    async def _daily_date_list(
        client: httpx.AsyncClient, data_type: str, start: dt.date, end: dt.date
    ) -> list[dict]:
        points: list[dict] = []
        for chunk_start, chunk_end in HealthRepository.split_date_range(start, end, QUERY_MAX_DAYS):
            points.extend(
                await HealthRepository._list(
                    client,
                    data_type,
                    HealthRepository._daily_date_filter(
                        data_type, chunk_start, chunk_end + dt.timedelta(days=1)
                    ),
                )
            )
        return points

    @staticmethod
    async def _sample_time_list(
        client: httpx.AsyncClient, data_type: str, start: dt.date, end: dt.date
    ) -> list[dict]:
        field = f"{data_type.replace('-', '_')}.sample_time.civil_time"
        points: list[dict] = []
        for chunk_start, chunk_end in HealthRepository.split_date_range(start, end, QUERY_MAX_DAYS):
            exclusive_end = chunk_end + dt.timedelta(days=1)
            points.extend(
                await HealthRepository._list(
                    client,
                    data_type,
                    f'{field} >= "{chunk_start.isoformat()}" '
                    f'AND {field} < "{exclusive_end.isoformat()}"',
                )
            )
        return points

    @staticmethod
    def _session_date_filter(data_type: str, start: dt.date, exclusive_end: dt.date) -> str:
        """セッションの日付 filter。filter ではデータ型名をアンダースコアで書く。"""
        field = f"{data_type.replace('-', '_')}.interval.civil_start_time"
        return f'{field} >= "{start.isoformat()}" AND {field} < "{exclusive_end.isoformat()}"'

    @staticmethod
    def _session_interval(start: dt.datetime, end: dt.datetime) -> dict:
        """SessionTimeInterval。開始・終了（UTC）と現地時刻のオフセットがいずれも必須。"""
        return {
            "startTime": HealthRepository._utc_timestamp(start),
            "endTime": HealthRepository._utc_timestamp(end),
            "startUtcOffset": HealthRepository._utc_offset(start),
            "endUtcOffset": HealthRepository._utc_offset(end),
        }

    @staticmethod
    def _utc_timestamp(value: dt.datetime) -> str:
        """google.protobuf.Timestamp の JSON 表現（"2026-01-31T03:00:00Z"）。"""
        return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _utc_offset(value: dt.datetime) -> str:
        """google.protobuf.Duration の JSON 表現（"32400s"）。"""
        offset = value.utcoffset() or dt.timedelta()
        return f"{int(offset.total_seconds())}s"

    @staticmethod
    def _nutrition_log(item: dict, meal_type: str, interval: dict) -> dict:
        """匿名方式（foodDisplayName と栄養素を直接指定）の NutritionLog。

        たんぱく質だけは nutrients の PROTEIN で、脂質・炭水化物は専用のフィールドで渡す。
        """
        log: dict = {
            "interval": interval,
            "mealType": meal_type,
            "foodDisplayName": item["food_name"],
            "energy": {"kcal": item["energy_kcal"]},
        }
        if item.get("protein_g") is not None:
            log["nutrients"] = [{"nutrient": "PROTEIN", "quantity": {"grams": item["protein_g"]}}]
        if item.get("fat_g") is not None:
            log["totalFat"] = {"grams": item["fat_g"]}
        if item.get("carbohydrate_g") is not None:
            log["totalCarbohydrate"] = {"grams": item["carbohydrate_g"]}
        return log

    @staticmethod
    def _operation_response(operation: dict) -> dict:
        """create・batchDelete が返す Operation から、作成されたデータポイントを取り出す。

        HTTP が 200 でも Operation.error に失敗が入る。done が false のうちは response が
        返らないので、作成された記録を特定できないまま空になる。
        """
        error = operation.get("error", {})
        if error:
            raise HealthApiError(
                f"Google Health APIの書き込みに失敗しました: {error.get('code')} "
                f"{error.get('message', '')}"
            )
        return operation.get("response", {})

    @staticmethod
    def _daily_date_filter(data_type: str, start: dt.date, exclusive_end: dt.date) -> str:
        """日次サマリーの日付 filter。filter ではデータ型名をアンダースコアで書く。"""
        field = f"{data_type.replace('-', '_')}.date"
        return f'{field} >= "{start.isoformat()}" AND {field} < "{exclusive_end.isoformat()}"'

    @staticmethod
    async def _list(
        client: httpx.AsyncClient, data_type: str, filter_: str, page_size: int | None = None
    ) -> list[dict]:
        points: list[dict] = []
        params: dict = {"filter": filter_}
        if page_size is not None:
            params["pageSize"] = page_size
        while True:
            raw = await HealthRepository._request(
                client, "GET", f"/dataTypes/{data_type}/dataPoints", params=params
            )
            points.extend(raw.get("dataPoints", []))
            next_page_token = raw.get("nextPageToken")
            if not next_page_token:
                return points
            params = {**params, "pageToken": next_page_token}

    @staticmethod
    def split_date_range(
        start: dt.date, end: dt.date, max_days: int
    ) -> list[tuple[dt.date, dt.date]]:
        """start・end の両日を含む区間を、max_days 日以下の隙間のない区間に分ける。"""
        chunks: list[tuple[dt.date, dt.date]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + dt.timedelta(days=max_days - 1), end)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + dt.timedelta(days=1)
        return chunks

    @staticmethod
    def _civil_date(day: dt.date) -> dict:
        return {"date": {"year": day.year, "month": day.month, "day": day.day}}
