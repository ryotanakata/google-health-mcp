"""リクエスト・レスポンスの形は公式ディスカバリドキュメントに準拠する。

https://health.googleapis.com/$discovery/rest?version=v4
"""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx

from src.constants import (
    HEALTH_API_BASE_URL,
    HEALTH_API_MAX_ERROR_BODY_CHARS,
    QUERY_MAX_DAYS,
    SESSION_PAGE_SIZE,
)
from src.google_auth import GoogleAuthManager


class HealthApiError(RuntimeError):
    pass


class HealthRepository:
    def __init__(
        self, auth_manager: GoogleAuthManager, base_url: str = HEALTH_API_BASE_URL
    ) -> None:
        self._auth_manager = auth_manager
        self._base_url = base_url

    async def fetch_activity_rollups(self, day: dt.date) -> dict[str, list[dict]]:
        data_types = ("steps", "distance", "total-calories", "active-minutes")
        results = await asyncio.gather(
            *(self._daily_rollup(data_type, day) for data_type in data_types)
        )
        return dict(zip(data_types, results, strict=True))

    async def fetch_sleep_sessions(self, day: dt.date) -> list[dict]:
        """day に終わった睡眠を返す（前夜〜当日朝の睡眠と当日の仮眠）。

        sleep は開始時刻での絞り込みに対応していないため、終了時刻で絞る。
        """
        next_day = day + dt.timedelta(days=1)
        return await self._list(
            "sleep",
            f'sleep.interval.civil_end_time >= "{day.isoformat()}" '
            f'AND sleep.interval.civil_end_time < "{next_day.isoformat()}"',
            page_size=SESSION_PAGE_SIZE,
        )

    async def fetch_heart_rate(self, day: dt.date) -> tuple[list[dict], list[dict]]:
        """(安静時心拍数のデータポイント, 心拍ゾーン滞在時間の日次集計) を返す。"""
        next_day = day + dt.timedelta(days=1)
        resting, zones = await asyncio.gather(
            self._list(
                "daily-resting-heart-rate",
                f'daily_resting_heart_rate.date >= "{day.isoformat()}" '
                f'AND daily_resting_heart_rate.date < "{next_day.isoformat()}"',
            ),
            self._daily_rollup("time-in-heart-rate-zone", day),
        )
        return resting, zones

    async def fetch_exercises(self, start: dt.date, end: dt.date) -> list[dict]:
        """start・end の両日を含む。"""
        points: list[dict] = []
        for chunk_start, chunk_end in self.split_date_range(start, end, QUERY_MAX_DAYS):
            exclusive_end = chunk_end + dt.timedelta(days=1)
            points.extend(
                await self._list(
                    "exercise",
                    f'exercise.interval.civil_start_time >= "{chunk_start.isoformat()}" '
                    f'AND exercise.interval.civil_start_time < "{exclusive_end.isoformat()}"',
                    page_size=SESSION_PAGE_SIZE,
                )
            )
        return points

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> dict:
        token = self._auth_manager.get_access_token()
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.request(
                method,
                path,
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code >= 400:
            raise HealthApiError(
                f"Google Health API呼び出しに失敗しました: {response.status_code} "
                f"{response.text[:HEALTH_API_MAX_ERROR_BODY_CHARS]}"
            )
        return response.json()

    async def _daily_rollup(self, data_type: str, day: dt.date) -> list[dict]:
        body = {
            "range": {
                "start": self._civil_date(day),
                "end": self._civil_date(day + dt.timedelta(days=1)),
            },
            "windowSizeDays": 1,
        }
        raw = await self._request("POST", f"/{data_type}/dataPoints:dailyRollUp", json=body)
        return raw.get("rollupDataPoints", [])

    async def _list(self, data_type: str, filter_: str, page_size: int | None = None) -> list[dict]:
        points: list[dict] = []
        params: dict = {"filter": filter_}
        if page_size is not None:
            params["pageSize"] = page_size
        while True:
            raw = await self._request("GET", f"/{data_type}/dataPoints", params=params)
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
        if start > end:
            raise ValueError("start_date must be on or before end_date")

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
