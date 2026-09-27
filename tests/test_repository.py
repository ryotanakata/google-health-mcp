from __future__ import annotations

import datetime as dt
import json

import pytest
import respx
from httpx import Response

from src.google_auth import GoogleAuthManager
from src.repository import HealthApiError, HealthRepository

BASE = "https://example.test/v4/users/me/dataTypes"


@pytest.fixture
def repository(settings, monkeypatch) -> HealthRepository:
    monkeypatch.setattr(GoogleAuthManager, "get_access_token", lambda self: "fake-token")
    return HealthRepository(GoogleAuthManager(settings), base_url=BASE)


def test_split_date_range_single_chunk():
    chunks = HealthRepository.split_date_range(
        dt.date(2026, 1, 1), dt.date(2026, 1, 10), max_days=90
    )
    assert chunks == [(dt.date(2026, 1, 1), dt.date(2026, 1, 10))]


def test_split_date_range_multiple_chunks():
    chunks = HealthRepository.split_date_range(
        dt.date(2026, 1, 1), dt.date(2026, 4, 15), max_days=90
    )
    assert len(chunks) == 2
    assert chunks[0] == (dt.date(2026, 1, 1), dt.date(2026, 3, 31))
    # 隙間・重複なく連続していること
    assert chunks[1] == (dt.date(2026, 4, 1), dt.date(2026, 4, 15))


def test_split_date_range_invalid_order():
    with pytest.raises(ValueError):
        HealthRepository.split_date_range(dt.date(2026, 2, 1), dt.date(2026, 1, 1), max_days=90)


@respx.mock
async def test_fetch_activity_rollups_calls_daily_rollup(repository):
    steps_route = respx.post(f"{BASE}/steps/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={"rollupDataPoints": [{"steps": {"countSum": "5000"}}]})
    )
    for data_type in ("distance", "total-calories", "active-minutes"):
        respx.post(f"{BASE}/{data_type}/dataPoints:dailyRollUp").mock(
            return_value=Response(200, json={})
        )

    result = await repository.fetch_activity_rollups(dt.date(2026, 1, 31))

    assert result["steps"] == [{"steps": {"countSum": "5000"}}]
    assert result["distance"] == []
    request = steps_route.calls.last.request
    assert request.headers["Authorization"] == "Bearer fake-token"
    assert json.loads(request.content) == {
        "range": {
            "start": {"date": {"year": 2026, "month": 1, "day": 31}},
            "end": {"date": {"year": 2026, "month": 2, "day": 1}},
        },
        "windowSizeDays": 1,
    }


@respx.mock
async def test_fetch_sleep_sessions_filters_by_civil_end_time(repository):
    route = respx.get(f"{BASE}/sleep/dataPoints").mock(
        return_value=Response(200, json={"dataPoints": []})
    )

    await repository.fetch_sleep_sessions(dt.date(2026, 1, 2))

    params = route.calls.last.request.url.params
    assert params["filter"] == (
        'sleep.interval.civil_end_time >= "2026-01-02" '
        'AND sleep.interval.civil_end_time < "2026-01-03"'
    )
    assert params["pageSize"] == "25"


@respx.mock
async def test_fetch_exercises_pages_and_splits_by_90_days(repository):
    def respond(request):
        if request.url.params.get("pageToken") == "next":
            return Response(200, json={"dataPoints": [{"exercise": {"displayName": "B"}}]})
        first_chunk = 'exercise.interval.civil_start_time >= "2026-01-01"'
        if request.url.params["filter"].startswith(first_chunk):
            return Response(
                200,
                json={"dataPoints": [{"exercise": {"displayName": "A"}}], "nextPageToken": "next"},
            )
        return Response(200, json={"dataPoints": [{"exercise": {"displayName": "C"}}]})

    route = respx.get(f"{BASE}/exercise/dataPoints").mock(side_effect=respond)

    points = await repository.fetch_exercises(dt.date(2026, 1, 1), dt.date(2026, 4, 15))

    # 90日単位の2チャンク + 1チャンク目の2ページ目
    assert route.call_count == 3
    assert [p["exercise"]["displayName"] for p in points] == ["A", "B", "C"]
    last_filter = route.calls.last.request.url.params["filter"]
    assert last_filter.endswith('exercise.interval.civil_start_time < "2026-04-16"')


@respx.mock
async def test_api_error_is_raised(repository):
    respx.get(f"{BASE}/sleep/dataPoints").mock(return_value=Response(403, text="forbidden"))
    with pytest.raises(HealthApiError, match="403"):
        await repository.fetch_sleep_sessions(dt.date(2026, 1, 2))
