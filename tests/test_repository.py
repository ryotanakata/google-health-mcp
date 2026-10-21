from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
import respx
from httpx import Response

from src.google_auth import GoogleAuthManager
from src.repository import HealthApiError, HealthRepository, InsufficientScopeError

BASE = "https://example.test/v4/users/me"
DATA_TYPES = f"{BASE}/dataTypes"


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
    steps_route = respx.post(f"{DATA_TYPES}/steps/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={"rollupDataPoints": [{"steps": {"countSum": "5000"}}]})
    )
    for data_type in (
        "distance",
        "total-calories",
        "active-minutes",
        "floors",
        "active-zone-minutes",
    ):
        respx.post(f"{DATA_TYPES}/{data_type}/dataPoints:dailyRollUp").mock(
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
    route = respx.get(f"{DATA_TYPES}/sleep/dataPoints").mock(
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
async def test_fetch_heart_rate_filters_resting_by_date(repository):
    route = respx.get(f"{DATA_TYPES}/daily-resting-heart-rate/dataPoints").mock(
        return_value=Response(200, json={"dataPoints": []})
    )
    respx.post(f"{DATA_TYPES}/time-in-heart-rate-zone/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={})
    )

    await repository.fetch_heart_rate(dt.date(2026, 1, 2))

    assert route.calls.last.request.url.params["filter"] == (
        'daily_resting_heart_rate.date >= "2026-01-02" '
        'AND daily_resting_heart_rate.date < "2026-01-03"'
    )


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

    route = respx.get(f"{DATA_TYPES}/exercise/dataPoints").mock(side_effect=respond)

    points = await repository.fetch_exercises(dt.date(2026, 1, 1), dt.date(2026, 4, 15))

    # 90日単位の2チャンク + 1チャンク目の2ページ目
    assert route.call_count == 3
    assert [p["exercise"]["displayName"] for p in points] == ["A", "B", "C"]
    last_filter = route.calls.last.request.url.params["filter"]
    assert last_filter.endswith('exercise.interval.civil_start_time < "2026-04-16"')


@respx.mock
async def test_api_error_is_raised(repository):
    respx.get(f"{DATA_TYPES}/sleep/dataPoints").mock(return_value=Response(403, text="forbidden"))
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
        routes[data_type] = respx.get(f"{DATA_TYPES}/{data_type}/dataPoints").mock(
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
    respx.get(url__regex=rf"{DATA_TYPES}/daily-.+/dataPoints").mock(
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

    weight_route = respx.get(f"{DATA_TYPES}/weight/dataPoints").mock(side_effect=respond_weight)
    respx.get(f"{DATA_TYPES}/body-fat/dataPoints").mock(return_value=Response(200, json={}))

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
            f"{DATA_TYPES}/{data_type}/dataPoints:dailyRollUp"
        ).mock(return_value=Response(200, json={"rollupDataPoints": [{"any": data_type}]}))
    resting_route = respx.get(f"{DATA_TYPES}/daily-resting-heart-rate/dataPoints").mock(
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

    route = respx.post(f"{DATA_TYPES}/steps/dataPoints:dailyRollUp").mock(side_effect=respond)
    for data_type in (
        "distance",
        "total-calories",
        "active-minutes",
        "floors",
        "active-zone-minutes",
    ):
        respx.post(f"{DATA_TYPES}/{data_type}/dataPoints:dailyRollUp").mock(
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
    respx.post(url__regex=rf"{DATA_TYPES}/.+/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={})
    )

    await repository.fetch_activity_rollups(dt.date(2026, 1, 31))

    # 6種類の並列リクエストが1つのクライアントを共有し、処理後に閉じられていること
    assert respx.calls.call_count == 6
    assert len(opened_clients) == 1
    assert opened_clients[0].is_closed


@respx.mock
async def test_paging_and_chunks_share_one_client(repository, opened_clients):
    respx.get(f"{DATA_TYPES}/exercise/dataPoints").mock(
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


def _jst(year: int, month: int, day: int, hour: int, minute: int) -> dt.datetime:
    return dt.datetime(
        year, month, day, hour, minute, tzinfo=dt.timezone(dt.timedelta(hours=9))
    )


def _operation(data_point: dict) -> dict:
    return {"done": True, "response": data_point}


@respx.mock
async def test_create_nutrition_logs_sends_anonymous_meal(repository):
    route = respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints").mock(
        return_value=Response(
            200, json=_operation({"name": "users/me/dataTypes/nutrition-log/dataPoints/a1"})
        )
    )

    created = await repository.create_nutrition_logs(
        "LUNCH",
        [
            {
                "food_name": "親子丼",
                "energy_kcal": 600.0,
                "protein_g": 30.0,
                "fat_g": 15.0,
                "carbohydrate_g": 80.0,
            }
        ],
        _jst(2026, 1, 31, 12, 30),
        _jst(2026, 1, 31, 12, 31),
    )

    assert created == [{"name": "users/me/dataTypes/nutrition-log/dataPoints/a1"}]
    log = json.loads(route.calls.last.request.content)["nutritionLog"]
    assert log["mealType"] == "LUNCH"
    assert log["foodDisplayName"] == "親子丼"
    assert log["energy"] == {"kcal": 600.0}
    # たんぱく質だけ nutrients、脂質・炭水化物は専用フィールド
    assert log["nutrients"] == [{"nutrient": "PROTEIN", "quantity": {"grams": 30.0}}]
    assert log["totalFat"] == {"grams": 15.0}
    assert log["totalCarbohydrate"] == {"grams": 80.0}
    # UTC に直した開始・終了と、現地時刻のオフセットが付くこと
    assert log["interval"] == {
        "startTime": "2026-01-31T03:30:00Z",
        "endTime": "2026-01-31T03:31:00Z",
        "startUtcOffset": "32400s",
        "endUtcOffset": "32400s",
    }
    assert log["interval"]["startTime"] < log["interval"]["endTime"]


@respx.mock
async def test_create_nutrition_logs_sends_one_request_per_item(repository):
    names = iter(["a1", "a2"])
    route = respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints").mock(
        side_effect=lambda request: Response(
            200,
            json=_operation({"name": f"users/me/dataTypes/nutrition-log/dataPoints/{next(names)}"}),
        )
    )

    created = await repository.create_nutrition_logs(
        "DINNER",
        [
            {"food_name": "カレー", "energy_kcal": 700.0},
            {"food_name": "サラダ", "energy_kcal": 50.0, "protein_g": 2.0},
        ],
        _jst(2026, 1, 31, 19, 0),
        _jst(2026, 1, 31, 19, 1),
    )

    assert route.call_count == 2
    assert [point["name"].rsplit("/", 1)[-1] for point in created] == ["a1", "a2"]
    first = json.loads(route.calls[0].request.content)["nutritionLog"]
    second = json.loads(route.calls[1].request.content)["nutritionLog"]
    assert first["foodDisplayName"] == "カレー"
    # 値のない栄養素のフィールドは送らない
    assert "nutrients" not in first
    assert second["nutrients"] == [{"nutrient": "PROTEIN", "quantity": {"grams": 2.0}}]


@respx.mock
async def test_create_hydration_log_sends_milliliters(repository):
    route = respx.post(f"{DATA_TYPES}/hydration-log/dataPoints").mock(
        return_value=Response(
            200, json=_operation({"name": "users/me/dataTypes/hydration-log/dataPoints/w1"})
        )
    )

    created = await repository.create_hydration_log(
        500.0, _jst(2026, 1, 31, 15, 0), _jst(2026, 1, 31, 15, 1)
    )

    assert created["name"] == "users/me/dataTypes/hydration-log/dataPoints/w1"
    log = json.loads(route.calls.last.request.content)["hydrationLog"]
    assert log["amountConsumed"] == {"milliliters": 500.0}
    assert log["interval"]["startTime"] == "2026-01-31T06:00:00Z"
    assert log["interval"]["startUtcOffset"] == "32400s"


@respx.mock
async def test_create_nutrition_logs_raises_when_operation_failed(repository):
    respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints").mock(
        return_value=Response(
            200, json={"done": True, "error": {"code": 3, "message": "invalid interval"}}
        )
    )

    with pytest.raises(HealthApiError, match="invalid interval"):
        await repository.create_nutrition_logs(
            "LUNCH",
            [{"food_name": "丼", "energy_kcal": 600.0}],
            _jst(2026, 1, 31, 12, 30),
            _jst(2026, 1, 31, 12, 31),
        )


@respx.mock
async def test_create_nutrition_logs_without_finished_operation(repository):
    """done が false のうちは作成された記録を特定できないので、name なしで返る。"""
    respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints").mock(
        return_value=Response(200, json={"name": "operations/1", "done": False})
    )

    created = await repository.create_nutrition_logs(
        "SNACK",
        [{"food_name": "ようかん", "energy_kcal": 150.0}],
        _jst(2026, 1, 31, 15, 0),
        _jst(2026, 1, 31, 15, 1),
    )

    assert created == [{}]


@respx.mock
async def test_delete_nutrition_data_points_groups_by_data_type(repository):
    meals = respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints:batchDelete").mock(
        return_value=Response(200, json={"done": True, "response": {}})
    )
    water = respx.post(f"{DATA_TYPES}/hydration-log/dataPoints:batchDelete").mock(
        return_value=Response(200, json={"done": True, "response": {}})
    )

    await repository.delete_nutrition_data_points(
        [
            "users/me/dataTypes/nutrition-log/dataPoints/a1",
            "users/me/dataTypes/hydration-log/dataPoints/w1",
            "users/me/dataTypes/nutrition-log/dataPoints/a2",
        ]
    )

    # batchDelete は parent と name の親が一致している必要があるので、データ型ごとに1回ずつ
    assert meals.call_count == 1
    assert water.call_count == 1
    assert json.loads(meals.calls.last.request.content) == {
        "names": [
            "users/me/dataTypes/nutrition-log/dataPoints/a1",
            "users/me/dataTypes/nutrition-log/dataPoints/a2",
        ]
    }
    assert json.loads(water.calls.last.request.content) == {
        "names": ["users/me/dataTypes/hydration-log/dataPoints/w1"]
    }


@respx.mock
async def test_delete_nutrition_data_points_rejects_other_data_types(repository):
    with pytest.raises(HealthApiError):
        await repository.delete_nutrition_data_points(
            ["users/me/dataTypes/sleep/dataPoints/s1"]
        )

    assert not respx.calls


@pytest.mark.parametrize(
    "name",
    [
        "users/me/dataTypes/nutrition-log/dataPoints/a1",
        "users/1234567890/dataTypes/hydration-log/dataPoints/w1",
    ],
)
def test_data_point_data_type_reads_the_data_type(name):
    assert HealthRepository.data_point_data_type(name) in ("nutrition-log", "hydration-log")


@pytest.mark.parametrize(
    "name",
    [
        "",
        "nutrition-log",
        "users/me/dataTypes/nutrition-log/dataPoints/",
        "users/me/dataTypes/nutrition-log/dataPoints/a1/extra",
        "projects/me/dataTypes/nutrition-log/dataPoints/a1",
        "users/me/other/nutrition-log/dataPoints/a1",
    ],
)
def test_data_point_data_type_rejects_other_shapes(name):
    assert HealthRepository.data_point_data_type(name) is None


@respx.mock
async def test_fetch_nutrition_log_reads_entries_and_totals(repository):
    meals = respx.get(f"{DATA_TYPES}/nutrition-log/dataPoints").mock(
        return_value=Response(
            200, json={"dataPoints": [{"nutritionLog": {"foodDisplayName": "親子丼"}}]}
        )
    )
    water = respx.get(f"{DATA_TYPES}/hydration-log/dataPoints").mock(
        return_value=Response(200, json={"dataPoints": [{"hydrationLog": {}}]})
    )
    meal_totals = respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints:dailyRollUp").mock(
        return_value=Response(
            200,
            json={"rollupDataPoints": [{"nutritionLog": {"energy": {"kcalSum": 1800.0}}}]},
        )
    )
    water_totals = respx.post(f"{DATA_TYPES}/hydration-log/dataPoints:dailyRollUp").mock(
        return_value=Response(
            200,
            json={
                "rollupDataPoints": [
                    {"hydrationLog": {"amountConsumed": {"millilitersSum": 1500.0}}}
                ]
            },
        )
    )

    result = await repository.fetch_nutrition_log(dt.date(2026, 1, 31))

    assert result["nutrition-log"] == [{"nutritionLog": {"foodDisplayName": "親子丼"}}]
    assert result["hydration-log"] == [{"hydrationLog": {}}]
    assert result["nutrition-log-rollup"][0]["nutritionLog"]["energy"]["kcalSum"] == 1800.0
    assert result["hydration-log-rollup"][0]["hydrationLog"]["amountConsumed"][
        "millilitersSum"
    ] == 1500.0
    # セッションのデータ型は現地時刻の開始時刻で絞る。filter ではアンダースコア
    assert meals.calls.last.request.url.params["filter"] == (
        'nutrition_log.interval.civil_start_time >= "2026-01-31" '
        'AND nutrition_log.interval.civil_start_time < "2026-02-01"'
    )
    assert water.calls.last.request.url.params["filter"] == (
        'hydration_log.interval.civil_start_time >= "2026-01-31" '
        'AND hydration_log.interval.civil_start_time < "2026-02-01"'
    )
    assert json.loads(meal_totals.calls.last.request.content)["windowSizeDays"] == 1
    assert water_totals.call_count == 1


@respx.mock
async def test_fetch_nutrition_log_without_records(repository):
    respx.get(f"{DATA_TYPES}/nutrition-log/dataPoints").mock(return_value=Response(200, json={}))
    respx.get(f"{DATA_TYPES}/hydration-log/dataPoints").mock(return_value=Response(200, json={}))
    respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={})
    )
    respx.post(f"{DATA_TYPES}/hydration-log/dataPoints:dailyRollUp").mock(
        return_value=Response(200, json={})
    )

    result = await repository.fetch_nutrition_log(dt.date(2026, 1, 31))

    assert result == {
        "nutrition-log": [],
        "hydration-log": [],
        "nutrition-log-rollup": [],
        "hydration-log-rollup": [],
    }


@respx.mock
async def test_fetch_paired_devices_pages_to_the_end(repository):
    route = respx.get(f"{BASE}/pairedDevices").mock(
        side_effect=[
            Response(
                200,
                json={"pairedDevices": [{"deviceVersion": "Fitbit Air"}], "nextPageToken": "n1"},
            ),
            Response(200, json={"pairedDevices": [{"deviceVersion": "Aria"}]}),
        ]
    )

    devices = await repository.fetch_paired_devices()

    assert devices == [{"deviceVersion": "Fitbit Air"}, {"deviceVersion": "Aria"}]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["pageToken"] == "n1"


@respx.mock
async def test_fetch_paired_devices_without_devices(repository):
    respx.get(f"{BASE}/pairedDevices").mock(return_value=Response(200, json={}))

    assert await repository.fetch_paired_devices() == []


@respx.mock
async def test_forbidden_asks_to_renew_the_refresh_token(repository):
    respx.get(f"{BASE}/pairedDevices").mock(
        return_value=Response(403, json={"error": {"message": "insufficient scope"}})
    )

    with pytest.raises(InsufficientScopeError, match="auth_setup.py") as error:
        await repository.fetch_paired_devices()

    assert "403" in str(error.value)


@respx.mock
async def test_forbidden_on_write_is_an_api_error(repository):
    """スコープ不足はどのツールでも同じ案内になること。"""
    respx.post(f"{DATA_TYPES}/nutrition-log/dataPoints").mock(return_value=Response(403, json={}))

    with pytest.raises(HealthApiError, match="refresh_token"):
        await repository.create_nutrition_logs(
            "LUNCH",
            [{"food_name": "丼", "energy_kcal": 600.0}],
            _jst(2026, 1, 31, 12, 30),
            _jst(2026, 1, 31, 12, 31),
        )
