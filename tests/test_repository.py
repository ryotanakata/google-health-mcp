from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
import respx
from httpx import Response

from src.google_auth import GoogleAuthManager
from src.repository import HealthApiError, HealthRepository

BASE = "https://example.test/v4/users/me/dataTypes"


@pytest.fixture
def repository(settings, monkeypatch) -> HealthRepository:
    async def fake_token(self):
        return "fake-token"

    monkeypatch.setattr(GoogleAuthManager, "get_access_token", fake_token)
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


def test_split_date_range_returns_empty_when_start_is_after_end():
    chunks = HealthRepository.split_date_range(
        dt.date(2026, 2, 1), dt.date(2026, 1, 1), max_days=90
    )
    assert chunks == []


@respx.mock
async def test_fetch_activity_rollups_calls_daily_rollup(repository):
    steps_route = respx.post(f"{BASE}/steps/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={"rollupDataPoints": [{"steps": {"countSum": "5000"}}]})
    )
    for data_type in (
        "distance",
        "total-calories",
        "active-minutes",
        "floors",
        "active-zone-minutes",
    ):
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


@respx.mock
async def test_fetch_daily_metrics_filters_each_data_type_by_date(repository):
    routes = {}
    for data_type in (
        "daily-heart-rate-variability",
        "daily-respiratory-rate",
        "daily-oxygen-saturation",
        "daily-sleep-temperature-derivations",
        "daily-vo2-max",
    ):
        routes[data_type] = respx.get(f"{BASE}/{data_type}/dataPoints").mock(
            return_value=Response(200, json={"dataPoints": [{"any": data_type}]})
        )

    result = await repository.fetch_daily_metrics(dt.date(2026, 1, 2))

    assert result["daily-vo2-max"] == [{"any": "daily-vo2-max"}]
    # filter ではデータ型名をアンダースコアで書く
    assert routes["daily-heart-rate-variability"].calls.last.request.url.params["filter"] == (
        'daily_heart_rate_variability.date >= "2026-01-02" '
        'AND daily_heart_rate_variability.date < "2026-01-03"'
    )


@respx.mock
async def test_fetch_daily_metrics_without_data(repository):
    respx.get(url__regex=rf"{BASE}/daily-.+/dataPoints").mock(
        return_value=Response(200, json={})
    )

    result = await repository.fetch_daily_metrics(dt.date(2026, 1, 2))

    assert len(result) == 5
    assert all(points == [] for points in result.values())


@respx.mock
async def test_fetch_body_measurements_filters_by_sample_civil_time_and_pages(repository):
    def respond_weight(request):
        if request.url.params.get("pageToken") == "next":
            return Response(200, json={"dataPoints": [{"weight": {"weightGrams": 69000.0}}]})
        return Response(
            200,
            json={
                "dataPoints": [{"weight": {"weightGrams": 70000.0}}],
                "nextPageToken": "next",
            },
        )

    weight_route = respx.get(f"{BASE}/weight/dataPoints").mock(side_effect=respond_weight)
    respx.get(f"{BASE}/body-fat/dataPoints").mock(return_value=Response(200, json={}))

    result = await repository.fetch_body_measurements(dt.date(2026, 1, 1), dt.date(2026, 1, 31))

    assert weight_route.call_count == 2
    assert [p["weight"]["weightGrams"] for p in result["weight"]] == [70000.0, 69000.0]
    assert result["body-fat"] == []
    assert weight_route.calls[0].request.url.params["filter"] == (
        'weight.sample_time.civil_time >= "2026-01-01" '
        'AND weight.sample_time.civil_time < "2026-02-01"'
    )


@respx.mock
async def test_fetch_activity_trend_splits_14_day_data_types(repository):
    rollup_routes = {}
    for data_type in (
        "steps",
        "distance",
        "total-calories",
        "active-minutes",
        "floors",
        "active-zone-minutes",
    ):
        rollup_routes[data_type] = respx.post(
            f"{BASE}/{data_type}/dataPoints:dailyRollUp"
        ).mock(return_value=Response(200, json={"rollupDataPoints": [{"any": data_type}]}))
    resting_route = respx.get(f"{BASE}/daily-resting-heart-rate/dataPoints").mock(
        return_value=Response(200, json={"dataPoints": []})
    )

    rollups, resting = await repository.fetch_activity_trend(
        dt.date(2026, 1, 1), dt.date(2026, 1, 20)
    )

    # 20日間は90日上限の型では1リクエスト、14日上限の型では2リクエストに分かれる
    assert rollup_routes["steps"].call_count == 1
    assert rollup_routes["floors"].call_count == 1
    assert rollup_routes["total-calories"].call_count == 2
    assert rollup_routes["active-minutes"].call_count == 2
    assert resting_route.call_count == 1
    assert resting == []
    assert rollups["total-calories"] == [{"any": "total-calories"}, {"any": "total-calories"}]

    # 分割した区間に隙間・重複がないこと（1〜14日 と 15〜20日）
    ranges = [
        json.loads(call.request.content)["range"]
        for call in rollup_routes["total-calories"].calls
    ]
    assert ranges == [
        {
            "start": {"date": {"year": 2026, "month": 1, "day": 1}},
            "end": {"date": {"year": 2026, "month": 1, "day": 15}},
        },
        {
            "start": {"date": {"year": 2026, "month": 1, "day": 15}},
            "end": {"date": {"year": 2026, "month": 1, "day": 21}},
        },
    ]
    steps_body = json.loads(rollup_routes["steps"].calls.last.request.content)
    assert steps_body["windowSizeDays"] == 1
    assert steps_body["range"]["end"] == {"date": {"year": 2026, "month": 1, "day": 21}}


@respx.mock
async def test_daily_rollup_pages_to_the_end(repository):
    def respond(request):
        if json.loads(request.content).get("pageToken") == "next":
            return Response(200, json={"rollupDataPoints": [{"second": True}]})
        return Response(
            200, json={"rollupDataPoints": [{"first": True}], "nextPageToken": "next"}
        )

    route = respx.post(f"{BASE}/steps/dataPoints:dailyRollUp").mock(side_effect=respond)
    for data_type in (
        "distance",
        "total-calories",
        "active-minutes",
        "floors",
        "active-zone-minutes",
    ):
        respx.post(f"{BASE}/{data_type}/dataPoints:dailyRollUp").mock(
            return_value=Response(200, json={})
        )

    result = await repository.fetch_activity_rollups(dt.date(2026, 1, 31))

    assert route.call_count == 2
    assert result["steps"] == [{"first": True}, {"second": True}]
    # ページトークン以外のリクエスト内容は1ページ目と同じであること
    first, second = (json.loads(call.request.content) for call in route.calls)
    assert second == {**first, "pageToken": "next"}


@pytest.fixture
def opened_clients(monkeypatch) -> list[httpx.AsyncClient]:
    """repository が生成した httpx.AsyncClient を記録する。"""
    clients: list[httpx.AsyncClient] = []
    original = httpx.AsyncClient

    def spy(*args, **kwargs):
        client = original(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("src.repository.httpx.AsyncClient", spy)
    return clients


@respx.mock
async def test_parallel_requests_share_one_client(repository, opened_clients):
    respx.post(url__regex=rf"{BASE}/.+/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={})
    )

    await repository.fetch_activity_rollups(dt.date(2026, 1, 31))

    # 6種類の並列リクエストが1つのクライアントを共有し、処理後に閉じられていること
    assert respx.calls.call_count == 6
    assert len(opened_clients) == 1
    assert opened_clients[0].is_closed


@respx.mock
async def test_paging_and_chunks_share_one_client(repository, opened_clients):
    respx.get(f"{BASE}/exercise/dataPoints").mock(
        side_effect=[
            Response(200, json={"dataPoints": [], "nextPageToken": "next"}),
            Response(200, json={"dataPoints": []}),
            Response(200, json={"dataPoints": []}),
        ]
    )

    await repository.fetch_exercises(dt.date(2026, 1, 1), dt.date(2026, 4, 15))

    assert respx.calls.call_count == 3
    assert len(opened_clients) == 1
    assert opened_clients[0].is_closed
