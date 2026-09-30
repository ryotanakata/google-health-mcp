from __future__ import annotations

import datetime as dt

import pytest

from src.service import HealthService


def _civil(year: int, month: int, day: int, **time: int) -> dict:
    return {"date": {"year": year, "month": month, "day": day}, "time": time}


def _exercise(name: str, start: dict) -> dict:
    return {"exercise": {"displayName": name, "interval": {"civilStartTime": start}}}


class FakeRepository:
    """HealthRepository の代わりに、受け取った引数を記録して固定値を返す。"""

    def __init__(self, exercises: list[dict] | None = None) -> None:
        self.exercises = exercises or []
        self.calls: list[tuple] = []

    async def fetch_activity_rollups(self, day: dt.date) -> dict[str, list[dict]]:
        self.calls.append(("activity", day))
        return {"steps": [{"steps": {"countSum": "1234"}}]}

    async def fetch_exercises(self, start: dt.date, end: dt.date) -> list[dict]:
        self.calls.append(("exercises", start, end))
        return self.exercises


def test_summarize_activity():
    result = HealthService.summarize_activity(
        "2026-01-01",
        {
            "steps": [{"steps": {"countSum": "8000"}}],
            "distance": [{"distance": {"millimetersSum": "5200000"}}],
            "total-calories": [{"totalCalories": {"kcalSum": 2200.4}}],
            "active-minutes": [
                {
                    "activeMinutes": {
                        "activeMinutesRollupByActivityLevel": [
                            {"activityLevel": "LIGHT", "activeMinutesSum": "120"},
                            {"activityLevel": "MODERATE", "activeMinutesSum": "20"},
                            {"activityLevel": "VIGOROUS", "activeMinutesSum": "15"},
                        ]
                    }
                }
            ],
        },
    )
    assert result["steps"] == 8000
    assert result["distance_km"] == 5.2
    assert result["calories_out_kcal"] == 2200
    assert result["active_minutes"] == 35
    assert result["active_minutes_by_level"]["light"] == 120


def test_summarize_activity_without_data():
    result = HealthService.summarize_activity("2026-01-01", {})
    assert result["steps"] is None
    assert result["distance_km"] is None
    assert result["active_minutes"] == 0


def test_summarize_sleep_picks_main_sleep_and_computes_efficiency():
    points = [
        {
            "sleep": {
                "interval": {
                    "civilStartTime": _civil(2026, 1, 1, hours=23),
                    "civilEndTime": _civil(2026, 1, 2, hours=7),
                },
                "summary": {
                    "minutesAsleep": "420",
                    "minutesInSleepPeriod": "480",
                    "stagesSummary": [
                        {"type": "DEEP", "minutes": "60"},
                        {"type": "REM", "minutes": "90"},
                    ],
                },
                "metadata": {"mainSleep": True},
            }
        },
        {"sleep": {"summary": {"minutesAsleep": "30"}, "metadata": {"nap": True}}},
    ]
    result = HealthService.summarize_sleep("2026-01-02", points)
    assert result["total_minutes_asleep"] == 450
    assert result["naps"] == 1
    main = result["main_sleep"]
    assert main["start"] == "2026-01-01T23:00"
    assert main["efficiency"] == 88
    assert main["stages_minutes"] == {"deep": 60, "rem": 90}


def test_summarize_sleep_uses_utc_time_and_offset_without_civil_time():
    points = [
        {
            "sleep": {
                "interval": {
                    "startTime": "2026-09-29T15:30:00Z",
                    "startUtcOffset": "32400s",
                    "endTime": "2026-09-29T21:05:30.123456789Z",
                    "endUtcOffset": "32400s",
                },
                "summary": {"minutesAsleep": "261", "minutesInSleepPeriod": "277"},
                "metadata": {"mainSleep": True},
            }
        }
    ]
    main = HealthService.summarize_sleep("2026-09-30", points)["main_sleep"]
    assert main["start"] == "2026-09-30T00:30"
    assert main["end"] == "2026-09-30T06:05"


def test_summarize_sleep_without_sessions():
    result = HealthService.summarize_sleep("2026-01-02", [])
    assert result["main_sleep"] is None
    assert result["total_minutes_asleep"] == 0


def test_summarize_heart_rate():
    result = HealthService.summarize_heart_rate(
        "2026-01-01",
        resting=[{"dailyRestingHeartRate": {"beatsPerMinute": "58"}}],
        zones=[
            {
                "timeInHeartRateZone": {
                    "timeInHeartRateZones": [
                        {"heartRateZone": "MODERATE", "duration": "1800s"},
                        {"heartRateZone": "PEAK", "duration": "300s"},
                    ]
                }
            }
        ],
    )
    assert result["resting_heart_rate"] == 58
    assert result["heart_rate_zone_minutes"] == {"moderate": 30, "peak": 5}


def test_summarize_exercise():
    result = HealthService.summarize_exercise(
        {
            "displayName": "HIIT",
            "exerciseType": "HIIT",
            "interval": {"civilStartTime": _civil(2026, 1, 5, hours=7, minutes=30)},
            "activeDuration": "2700s",
            "metricsSummary": {
                "caloriesKcal": 512.6,
                "averageHeartRateBeatsPerMinute": "148",
                "distanceMillimeters": 1500000,
            },
        }
    )
    assert result["start"] == "2026-01-05T07:30"
    assert result["active_minutes"] == 45
    assert result["calories_kcal"] == 513
    assert result["average_heart_rate"] == 148
    assert result["distance_km"] == 1.5


def test_summarize_exercise_uses_utc_time_and_negative_offset_without_civil_time():
    result = HealthService.summarize_exercise(
        {
            "interval": {
                "startTime": "2026-01-05T02:30:00Z",
                "startUtcOffset": "-25200s",
                "endTime": "2026-01-05T03:15:00Z",
                "endUtcOffset": "-25200s",
            }
        }
    )
    assert result["start"] == "2026-01-04T19:30"
    assert result["end"] == "2026-01-04T20:15"


def test_summarize_exercise_prefers_civil_time_over_utc_time():
    result = HealthService.summarize_exercise(
        {
            "interval": {
                "civilStartTime": _civil(2026, 1, 5, hours=7, minutes=30),
                "startTime": "2026-01-04T22:30:00Z",
                "startUtcOffset": "0s",
            }
        }
    )
    assert result["start"] == "2026-01-05T07:30"
    assert result["end"] is None


async def test_get_exercise_history_sorts_sessions_without_civil_time():
    def exercise(name: str, start_time: str) -> dict:
        return {
            "exercise": {
                "displayName": name,
                "interval": {"startTime": start_time, "startUtcOffset": "32400s"},
            }
        }

    repository = FakeRepository(
        exercises=[
            exercise("later", "2026-09-20T10:00:00Z"),
            exercise("earlier", "2026-09-18T10:00:00Z"),
        ]
    )
    result = await HealthService(repository).get_exercise_history("2026-09-16", "2026-09-30")

    assert [s["name"] for s in result["sessions"]] == ["earlier", "later"]
    assert result["sessions"][0]["start"] == "2026-09-18T19:00"


async def test_get_daily_activity_parses_date_and_summarizes():
    repository = FakeRepository()
    result = await HealthService(repository).get_daily_activity("2026-01-31")

    assert repository.calls == [("activity", dt.date(2026, 1, 31))]
    assert result["steps"] == 1234


async def test_get_exercise_history_sorts_sessions_by_start():
    repository = FakeRepository(
        exercises=[
            _exercise("later", _civil(2026, 1, 9)),
            _exercise("earlier", _civil(2026, 1, 2)),
        ]
    )
    result = await HealthService(repository).get_exercise_history("2026-01-01", "2026-01-31")

    assert [s["name"] for s in result["sessions"]] == ["earlier", "later"]
    assert result["count"] == 2
    assert repository.calls == [("exercises", dt.date(2026, 1, 1), dt.date(2026, 1, 31))]


async def test_invalid_date_is_rejected():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        await HealthService(FakeRepository()).get_daily_activity("2026/01/01")


async def test_reversed_range_is_rejected():
    repository = FakeRepository()
    with pytest.raises(ValueError, match="^start_date には end_date 以前の日付を指定してください$"):
        await HealthService(repository).get_exercise_history("2026-02-01", "2026-01-01")
    assert repository.calls == []


async def test_range_up_to_max_days_is_accepted():
    repository = FakeRepository()
    await HealthService(repository).get_exercise_history("2025-01-01", "2026-01-01")
    assert repository.calls == [("exercises", dt.date(2025, 1, 1), dt.date(2026, 1, 1))]


async def test_range_over_max_days_is_rejected():
    repository = FakeRepository()
    with pytest.raises(ValueError, match="366 日以内"):
        await HealthService(repository).get_exercise_history("2025-01-01", "2026-01-02")
    assert repository.calls == []
