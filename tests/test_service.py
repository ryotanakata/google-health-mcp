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

    async def fetch_daily_metrics(self, day: dt.date) -> dict[str, list[dict]]:
        self.calls.append(("daily_metrics", day))
        return {"daily-respiratory-rate": [{"dailyRespiratoryRate": {"breathsPerMinute": 14.25}}]}

    async def fetch_body_measurements(self, start: dt.date, end: dt.date) -> dict[str, list[dict]]:
        self.calls.append(("body_measurements", start, end))
        return {}

    async def fetch_activity_trend(
        self, start: dt.date, end: dt.date
    ) -> tuple[dict[str, list[dict]], list[dict]]:
        self.calls.append(("activity_trend", start, end))
        return {}, []


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
    assert result["elevated_heart_rate_minutes"] == 35


def _zones(**minutes: int) -> list[dict]:
    return [
        {
            "timeInHeartRateZone": {
                "timeInHeartRateZones": [
                    {"heartRateZone": zone, "duration": f"{value * 60}s"}
                    for zone, value in minutes.items()
                ]
            }
        }
    ]


def test_summarize_heart_rate_excludes_light_zone():
    zones = _zones(LIGHT=1355, MODERATE=38, VIGOROUS=45, PEAK=2)
    result = HealthService.summarize_heart_rate("2026-09-27", resting=[], zones=zones)
    assert result["heart_rate_zone_minutes"] == {"moderate": 38, "vigorous": 45, "peak": 2}
    assert result["elevated_heart_rate_minutes"] == 85


def test_summarize_heart_rate_with_only_light_zone():
    result = HealthService.summarize_heart_rate("2026-09-29", resting=[], zones=_zones(LIGHT=1440))
    assert result["heart_rate_zone_minutes"] == {}
    assert result["elevated_heart_rate_minutes"] == 0


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


def test_summarize_exercise_treats_missing_active_zone_minutes_as_zero_with_heart_rate():
    result = HealthService.summarize_exercise(
        {"metricsSummary": {"averageHeartRateBeatsPerMinute": "82", "steps": "2588"}}
    )
    assert result["active_zone_minutes"] == 0


def test_summarize_exercise_keeps_active_zone_minutes_none_without_heart_rate():
    result = HealthService.summarize_exercise({"metricsSummary": {"caloriesKcal": 90.0}})
    assert result["active_zone_minutes"] is None
    assert result["steps"] is None
    assert result["distance_km"] is None


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


def _rollup(year: int, month: int, day: int, **values: dict) -> dict:
    return {"civilStartTime": {"date": {"year": year, "month": month, "day": day}}, **values}


def _weight(grams: float, physical_time: str, utc_offset: str = "32400s") -> dict:
    return {
        "weight": {
            "weightGrams": grams,
            "sampleTime": {"physicalTime": physical_time, "utcOffset": utc_offset},
        }
    }


def test_summarize_activity_includes_floors_and_active_zone_minutes():
    result = HealthService.summarize_activity(
        "2026-01-01",
        {
            "floors": [{"floors": {"countSum": "12"}}],
            "active-zone-minutes": [
                {
                    "activeZoneMinutes": {
                        "sumInFatBurnHeartZone": "20",
                        "sumInCardioHeartZone": "10",
                        "sumInPeakHeartZone": "4",
                    }
                }
            ],
        },
    )
    assert result["floors"] == 12
    assert result["active_zone_minutes_by_zone"] == {"fat_burn": 20, "cardio": 10, "peak": 4}
    assert result["active_zone_minutes"] == 34


def test_summarize_activity_without_floors_and_active_zone_minutes():
    result = HealthService.summarize_activity("2026-01-01", {})
    assert result["floors"] is None
    assert result["active_zone_minutes_by_zone"] == {}
    assert result["active_zone_minutes"] == 0


def test_summarize_activity_keeps_existing_fields_unchanged():
    rollups = {
        "steps": [{"steps": {"countSum": "8000"}}],
        "distance": [{"distance": {"millimetersSum": "5200000"}}],
        "total-calories": [{"totalCalories": {"kcalSum": 2200.4}}],
        "active-minutes": [
            {
                "activeMinutes": {
                    "activeMinutesRollupByActivityLevel": [
                        {"activityLevel": "MODERATE", "activeMinutesSum": "20"},
                        {"activityLevel": "VIGOROUS", "activeMinutesSum": "15"},
                    ]
                }
            }
        ],
    }
    result = HealthService.summarize_activity("2026-01-01", rollups)
    assert result["steps"] == 8000
    assert result["distance_km"] == 5.2
    assert result["calories_out_kcal"] == 2200
    assert result["active_minutes"] == 35
    assert result["active_minutes_by_level"] == {"moderate": 20, "vigorous": 15}


def test_summarize_health_metrics():
    result = HealthService.summarize_health_metrics(
        "2026-01-02",
        {
            "daily-heart-rate-variability": [
                {
                    "dailyHeartRateVariability": {
                        "averageHeartRateVariabilityMilliseconds": 42.456,
                        "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": 55.21,
                        "nonRemHeartRateBeatsPerMinute": "54",
                    }
                }
            ],
            "daily-respiratory-rate": [{"dailyRespiratoryRate": {"breathsPerMinute": 14.25}}],
            "daily-oxygen-saturation": [
                {
                    "dailyOxygenSaturation": {
                        "averagePercentage": 96.44,
                        "lowerBoundPercentage": 93.1,
                        "upperBoundPercentage": 99.02,
                    }
                }
            ],
            "daily-sleep-temperature-derivations": [
                {
                    "dailySleepTemperatureDerivations": {
                        "nightlyTemperatureCelsius": 36.12,
                        "baselineTemperatureCelsius": 35.9,
                    }
                }
            ],
            "daily-vo2-max": [
                {
                    "dailyVo2Max": {
                        "vo2Max": 41.78,
                        "cardioFitnessLevel": "GOOD",
                        "estimated": True,
                    }
                }
            ],
        },
    )
    assert result["date"] == "2026-01-02"
    assert result["heart_rate_variability"] == {
        "average_milliseconds": 42.5,
        "deep_sleep_rmssd_milliseconds": 55.2,
        "non_rem_heart_rate": 54,
    }
    assert result["respiratory_rate_breaths_per_minute"] == 14.2
    assert result["oxygen_saturation"]["average_percentage"] == 96.4
    assert result["skin_temperature"] == {
        "nightly_celsius": 36.12,
        "baseline_celsius": 35.9,
        "difference_celsius": 0.22,
    }
    assert result["vo2_max"] == {
        "vo2_max": 41.8,
        "cardio_fitness_level": "GOOD",
        "estimated": True,
    }


def test_summarize_health_metrics_with_partial_data():
    result = HealthService.summarize_health_metrics(
        "2026-01-02",
        {
            "daily-heart-rate-variability": [
                {"dailyHeartRateVariability": {"averageHeartRateVariabilityMilliseconds": 40.0}}
            ],
            "daily-respiratory-rate": [],
        },
    )
    assert result["heart_rate_variability"] == {
        "average_milliseconds": 40.0,
        "deep_sleep_rmssd_milliseconds": None,
        "non_rem_heart_rate": None,
    }
    assert result["respiratory_rate_breaths_per_minute"] is None
    assert result["oxygen_saturation"] is None
    assert result["skin_temperature"] is None
    assert result["vo2_max"] is None


def test_summarize_health_metrics_without_data():
    result = HealthService.summarize_health_metrics("2026-01-02", {})
    assert result == {
        "date": "2026-01-02",
        "heart_rate_variability": None,
        "respiratory_rate_breaths_per_minute": None,
        "oxygen_saturation": None,
        "skin_temperature": None,
        "vo2_max": None,
    }


def test_summarize_body_measurements_converts_grams_and_keeps_last_record_of_day():
    points = {
        "weight": [
            # 現地時刻（+9時間）では3件とも翌日になり、1月2日は2件目が最後の記録になる
            _weight(70000.0, "2026-01-01T22:00:00Z"),
            _weight(69500.0, "2026-01-02T12:00:00Z"),
            _weight(69000.0, "2026-01-03T22:00:00Z"),
        ],
        "body-fat": [
            {
                "bodyFat": {
                    "percentage": 18.46,
                    "sampleTime": {
                        "physicalTime": "2026-01-02T12:00:00Z",
                        "utcOffset": "32400s",
                    },
                }
            }
        ],
    }
    result = HealthService.summarize_body_measurements(
        dt.date(2026, 1, 1), dt.date(2026, 1, 31), points
    )
    assert result["start_date"] == "2026-01-01"
    assert result["end_date"] == "2026-01-31"
    assert result["days"] == [
        {"date": "2026-01-02", "weight_kg": 69.5, "body_fat_percentage": 18.5},
        {"date": "2026-01-04", "weight_kg": 69.0, "body_fat_percentage": None},
    ]
    assert result["weight_kg"] == {
        "first": 69.5,
        "last": 69.0,
        "min": 69.0,
        "max": 69.5,
        "average": 69.25,
        "count": 2,
    }
    assert result["body_fat_percentage"]["count"] == 1


def test_summarize_body_measurements_prefers_civil_time():
    points = {
        "weight": [
            {
                "weight": {
                    "weightGrams": 71234.0,
                    "sampleTime": {
                        "civilTime": {"date": {"year": 2026, "month": 2, "day": 3}},
                        "physicalTime": "2026-02-02T20:00:00Z",
                        "utcOffset": "0s",
                    },
                }
            }
        ]
    }
    result = HealthService.summarize_body_measurements(
        dt.date(2026, 2, 1), dt.date(2026, 2, 28), points
    )
    assert [day["date"] for day in result["days"]] == ["2026-02-03"]
    assert result["days"][0]["weight_kg"] == 71.23


def test_summarize_body_measurements_without_records():
    result = HealthService.summarize_body_measurements(
        dt.date(2026, 1, 1), dt.date(2026, 1, 31), {"weight": [], "body-fat": []}
    )
    assert result["days"] == []
    assert result["weight_kg"] == {
        "first": None,
        "last": None,
        "min": None,
        "max": None,
        "average": None,
        "count": 0,
    }
    assert result["body_fat_percentage"]["count"] == 0


def test_summarize_activity_trend_returns_every_day_with_totals_and_averages():
    rollups = {
        "steps": [
            _rollup(2026, 1, 1, steps={"countSum": "8000"}),
            _rollup(2026, 1, 2, steps={"countSum": "6000"}),
        ],
        "distance": [_rollup(2026, 1, 1, distance={"millimetersSum": "5000000"})],
        "total-calories": [_rollup(2026, 1, 1, totalCalories={"kcalSum": 2200.4})],
        "active-minutes": [
            _rollup(
                2026,
                1,
                1,
                activeMinutes={
                    "activeMinutesRollupByActivityLevel": [
                        {"activityLevel": "LIGHT", "activeMinutesSum": "100"},
                        {"activityLevel": "MODERATE", "activeMinutesSum": "20"},
                        {"activityLevel": "VIGOROUS", "activeMinutesSum": "10"},
                    ]
                },
            )
        ],
        "floors": [_rollup(2026, 1, 1, floors={"countSum": "12"})],
        "active-zone-minutes": [
            _rollup(
                2026,
                1,
                1,
                activeZoneMinutes={
                    "sumInFatBurnHeartZone": "20",
                    "sumInCardioHeartZone": "10",
                },
            )
        ],
    }
    resting = [
        {"dailyRestingHeartRate": {"date": {"year": 2026, "month": 1, "day": 1},
                                   "beatsPerMinute": "58"}},
        {"dailyRestingHeartRate": {"date": {"year": 2026, "month": 1, "day": 2},
                                   "beatsPerMinute": "60"}},
    ]
    result = HealthService.summarize_activity_trend(
        dt.date(2026, 1, 1), dt.date(2026, 1, 3), rollups, resting
    )

    # 期間の全日が1件ずつ、欠けも重複もなく並ぶこと
    assert [day["date"] for day in result["days"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert result["days"][0] == {
        "date": "2026-01-01",
        "steps": 8000,
        "distance_km": 5.0,
        "calories_out_kcal": 2200,
        "active_minutes": 30,
        "floors": 12,
        "active_zone_minutes": 30,
        "resting_heart_rate": 58,
    }
    assert result["totals"] == {
        "steps": 14000,
        "distance_km": 5.0,
        "calories_out_kcal": 2200,
        "active_minutes": 30,
        "floors": 12,
        "active_zone_minutes": 30,
    }
    # 平均はデータのある日だけで割る（歩数は2日、安静時心拍数は2日、距離は1日）
    assert result["averages"]["steps"] == 7000.0
    assert result["averages"]["distance_km"] == 5.0
    assert result["averages"]["resting_heart_rate"] == 59.0


def test_summarize_activity_trend_marks_days_without_data():
    result = HealthService.summarize_activity_trend(
        dt.date(2026, 1, 1), dt.date(2026, 1, 2), {}, []
    )
    assert result["days"] == [
        {
            "date": "2026-01-01",
            "steps": None,
            "distance_km": None,
            "calories_out_kcal": None,
            "active_minutes": None,
            "floors": None,
            "active_zone_minutes": None,
            "resting_heart_rate": None,
        },
        {
            "date": "2026-01-02",
            "steps": None,
            "distance_km": None,
            "calories_out_kcal": None,
            "active_minutes": None,
            "floors": None,
            "active_zone_minutes": None,
            "resting_heart_rate": None,
        },
    ]
    assert result["totals"]["steps"] is None
    assert result["averages"]["steps"] is None


async def test_get_health_metrics_parses_date_and_summarizes():
    repository = FakeRepository()
    result = await HealthService(repository).get_health_metrics("2026-01-02")

    assert repository.calls == [("daily_metrics", dt.date(2026, 1, 2))]
    assert result["respiratory_rate_breaths_per_minute"] == 14.2


async def test_get_body_measurements_passes_range_to_repository():
    repository = FakeRepository()
    result = await HealthService(repository).get_body_measurements("2026-01-01", "2026-01-31")

    assert repository.calls == [
        ("body_measurements", dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    ]
    assert result["days"] == []


async def test_get_activity_trend_passes_range_to_repository():
    repository = FakeRepository()
    result = await HealthService(repository).get_activity_trend("2026-01-01", "2026-01-07")

    assert repository.calls == [("activity_trend", dt.date(2026, 1, 1), dt.date(2026, 1, 7))]
    assert len(result["days"]) == 7


async def test_body_measurements_range_over_max_days_is_rejected():
    repository = FakeRepository()
    with pytest.raises(ValueError, match="90 日以内"):
        await HealthService(repository).get_body_measurements("2026-01-01", "2026-04-01")
    assert repository.calls == []


async def test_activity_trend_range_over_max_days_is_rejected():
    repository = FakeRepository()
    with pytest.raises(ValueError, match="90 日以内"):
        await HealthService(repository).get_activity_trend("2026-01-01", "2026-04-01")
    assert repository.calls == []


async def test_activity_trend_reversed_range_is_rejected():
    repository = FakeRepository()
    with pytest.raises(ValueError, match="^start_date には end_date 以前の日付を指定してください$"):
        await HealthService(repository).get_activity_trend("2026-02-01", "2026-01-01")
    assert repository.calls == []


async def test_activity_trend_range_up_to_max_days_is_accepted():
    repository = FakeRepository()
    await HealthService(repository).get_activity_trend("2026-01-01", "2026-03-31")
    assert repository.calls == [("activity_trend", dt.date(2026, 1, 1), dt.date(2026, 3, 31))]


def test_summarize_health_metrics_treats_missing_estimated_as_false():
    result = HealthService.summarize_health_metrics(
        "2026-01-02",
        {"daily-vo2-max": [{"dailyVo2Max": {"vo2Max": 41.0, "cardioFitnessLevel": "GOOD"}}]},
    )
    assert result["vo2_max"] == {
        "vo2_max": 41.0,
        "cardio_fitness_level": "GOOD",
        "estimated": False,
    }


def test_summarize_body_measurements_drops_records_outside_the_range():
    points = {
        "weight": [
            _weight(70000.0, "2025-12-01T12:00:00Z"),
            _weight(69000.0, "2026-01-10T12:00:00Z"),
            _weight(68000.0, "2026-03-01T12:00:00Z"),
        ],
        "body-fat": [
            {
                "bodyFat": {
                    "percentage": 18.0,
                    "sampleTime": {
                        "physicalTime": "2025-12-01T12:00:00Z",
                        "utcOffset": "32400s",
                    },
                }
            }
        ],
    }
    result = HealthService.summarize_body_measurements(
        dt.date(2026, 1, 1), dt.date(2026, 1, 31), points
    )
    assert [day["date"] for day in result["days"]] == ["2026-01-10"]
    assert result["weight_kg"]["count"] == 1
    assert result["weight_kg"]["min"] == 69.0
    assert result["body_fat_percentage"]["count"] == 0


def test_summarize_activity_trend_is_continuous_across_the_14_day_split():
    # rollup は1リクエスト14日が上限なので、15日間は2回に分割して取得される
    rollups = {
        "total-calories": [
            _rollup(2026, 1, 14, totalCalories={"kcalSum": 2100.0}),
            _rollup(2026, 1, 15, totalCalories={"kcalSum": 2200.0}),
        ]
    }
    result = HealthService.summarize_activity_trend(
        dt.date(2026, 1, 1), dt.date(2026, 1, 15), rollups, []
    )
    dates = [day["date"] for day in result["days"]]
    assert len(dates) == 15
    assert dates == sorted(set(dates))
    assert dates[0] == "2026-01-01" and dates[-1] == "2026-01-15"
    by_date = {day["date"]: day["calories_out_kcal"] for day in result["days"]}
    assert by_date["2026-01-14"] == 2100
    assert by_date["2026-01-15"] == 2200
    assert result["totals"]["calories_out_kcal"] == 4300


async def test_body_measurements_range_up_to_max_days_is_accepted():
    repository = FakeRepository()
    await HealthService(repository).get_body_measurements("2026-01-01", "2026-03-31")
    assert repository.calls == [
        ("body_measurements", dt.date(2026, 1, 1), dt.date(2026, 3, 31))
    ]


async def test_body_measurements_accepts_a_single_day():
    repository = FakeRepository()
    result = await HealthService(repository).get_body_measurements("2026-01-01", "2026-01-01")
    assert result["start_date"] == "2026-01-01"
    assert result["end_date"] == "2026-01-01"


async def test_body_measurements_reversed_range_is_rejected():
    repository = FakeRepository()
    with pytest.raises(ValueError, match="^start_date には end_date 以前の日付を指定してください$"):
        await HealthService(repository).get_body_measurements("2026-02-01", "2026-01-01")
    assert repository.calls == []


class FakeNutritionRepository:
    """食事・水分まわりの呼び出しだけを記録する。"""

    def __init__(
        self,
        nutrition: dict | None = None,
        devices: list[dict] | None = None,
        delete_completed: bool = True,
    ) -> None:
        self.nutrition = nutrition or {}
        self.devices = devices or []
        self.delete_completed = delete_completed
        self.calls: list[tuple] = []

    async def fetch_nutrition_log(self, day: dt.date) -> dict[str, list[dict]]:
        self.calls.append(("nutrition_log", day))
        return self.nutrition

    async def fetch_paired_devices(self) -> list[dict]:
        self.calls.append(("paired_devices",))
        return self.devices

    async def create_nutrition_logs(
        self, meal_type: str, items: list[dict], start: dt.datetime, end: dt.datetime
    ) -> list[dict]:
        self.calls.append(("create_nutrition", meal_type, items, start, end))
        return [
            {"name": f"users/me/dataTypes/nutrition-log/dataPoints/a{index}"}
            for index, _ in enumerate(items)
        ]

    async def create_hydration_log(
        self, amount_ml: float, start: dt.datetime, end: dt.datetime
    ) -> dict:
        self.calls.append(("create_hydration", amount_ml, start, end))
        return {"name": "users/me/dataTypes/hydration-log/dataPoints/w1"}

    async def delete_nutrition_data_points(self, names: list[str]) -> bool:
        self.calls.append(("delete", names))
        return self.delete_completed


def _meal_point(name: str, food: str, hours: int, **nutrients: float) -> dict:
    log: dict = {
        "foodDisplayName": food,
        "mealType": "LUNCH",
        "interval": {"civilStartTime": _civil(2026, 1, 31, hours=hours)},
        "energy": {"kcal": nutrients.get("kcal", 0.0)},
    }
    if "protein" in nutrients:
        log["nutrients"] = [{"nutrient": "PROTEIN", "quantity": {"grams": nutrients["protein"]}}]
    if "fat" in nutrients:
        log["totalFat"] = {"grams": nutrients["fat"]}
    if "carbohydrate" in nutrients:
        log["totalCarbohydrate"] = {"grams": nutrients["carbohydrate"]}
    return {"name": name, "nutritionLog": log}


def test_parse_meal_type_accepts_the_four_meal_types():
    assert HealthService.parse_meal_type("lunch") == "LUNCH"
    assert HealthService.parse_meal_type("SNACK") == "SNACK"


@pytest.mark.parametrize("value", ["ANYTIME", "BEFORE_LUNCH", "brunch", ""])
def test_parse_meal_type_rejects_other_values(value):
    with pytest.raises(ValueError, match="meal_type"):
        HealthService.parse_meal_type(value)


def test_parse_log_interval_uses_now_when_omitted():
    now = dt.datetime(2026, 1, 31, 12, 30, 45, 123, tzinfo=dt.timezone(dt.timedelta(hours=9)))

    start, end = HealthService.parse_log_interval(None, None, now)

    assert start == dt.datetime(2026, 1, 31, 12, 30, 45, tzinfo=now.tzinfo)
    # API は startTime < endTime を要求する
    assert start < end
    assert (end - start) == dt.timedelta(minutes=1)


def test_parse_log_interval_uses_the_given_date_and_time():
    now = dt.datetime(2026, 1, 31, 12, 30, tzinfo=dt.timezone(dt.timedelta(hours=9)))

    start, end = HealthService.parse_log_interval("2026-01-01", "07:15", now)

    assert start == dt.datetime(2026, 1, 1, 7, 15, tzinfo=now.tzinfo)
    assert end == dt.datetime(2026, 1, 1, 7, 16, tzinfo=now.tzinfo)


@pytest.mark.parametrize("value", ["7時", "25:00", "07:15+09:00", ""])
def test_parse_time_rejects_other_formats(value):
    with pytest.raises(ValueError, match="時刻"):
        HealthService.parse_time(value)


def test_parse_meal_items_requires_a_food_name_and_energy():
    with pytest.raises(ValueError, match="items"):
        HealthService.parse_meal_items([])
    with pytest.raises(ValueError, match="food_name"):
        HealthService.parse_meal_items([{"food_name": "  ", "energy_kcal": 1.0}])
    with pytest.raises(ValueError, match="energy_kcal"):
        HealthService.parse_meal_items([{"food_name": "丼"}])


def test_parse_meal_items_rejects_negative_values():
    with pytest.raises(ValueError, match="protein_g"):
        HealthService.parse_meal_items(
            [{"food_name": "丼", "energy_kcal": 600.0, "protein_g": -1.0}]
        )


def test_parse_meal_items_keeps_missing_macros_as_none():
    items = HealthService.parse_meal_items([{"food_name": " 親子丼 ", "energy_kcal": 600}])

    assert items == [
        {
            "food_name": "親子丼",
            "energy_kcal": 600.0,
            "protein_g": None,
            "fat_g": None,
            "carbohydrate_g": None,
        }
    ]


@pytest.mark.parametrize(
    "name",
    [
        "users/me/dataTypes/sleep/dataPoints/s1",
        "users/me/dataTypes/weight/dataPoints/w1",
        "users/me/dataTypes/nutrition-log/dataPoints",
        "nutrition-log",
    ],
)
async def test_delete_nutrition_logs_rejects_other_data_types_without_calling_the_api(name):
    repository = FakeNutritionRepository()
    service = HealthService(repository)

    with pytest.raises(ValueError, match="削除できるのは"):
        await service.delete_nutrition_logs([name])

    assert repository.calls == []


async def test_delete_nutrition_logs_requires_a_name():
    repository = FakeNutritionRepository()

    with pytest.raises(ValueError, match="names"):
        await HealthService(repository).delete_nutrition_logs([])

    assert repository.calls == []


async def test_delete_nutrition_logs_deletes_meals_and_hydration():
    repository = FakeNutritionRepository()
    names = [
        "users/me/dataTypes/nutrition-log/dataPoints/a1",
        "users/me/dataTypes/hydration-log/dataPoints/w1",
    ]

    result = await HealthService(repository).delete_nutrition_logs(names)

    assert repository.calls == [("delete", names)]
    assert result["count"] == 2
    assert result["names"] == names
    assert "2 件" in result["summary"]


async def test_log_meal_records_every_item_in_one_call():
    repository = FakeNutritionRepository()

    result = await HealthService(repository).log_meal(
        "lunch",
        [
            {"food_name": "親子丼", "energy_kcal": 600, "protein_g": 30, "carbohydrate_g": 80},
            {"food_name": "サラダ", "energy_kcal": 50},
        ],
        date="2026-01-31",
        time="12:30",
    )

    _, meal_type, items, start, end = repository.calls[0]
    assert meal_type == "LUNCH"
    assert [item["food_name"] for item in items] == ["親子丼", "サラダ"]
    assert start < end
    assert result["total_energy_kcal"] == 650.0
    assert result["logged_at"] == "2026-01-31T12:30"
    assert [record["name"] for record in result["records"]] == [
        "users/me/dataTypes/nutrition-log/dataPoints/a0",
        "users/me/dataTypes/nutrition-log/dataPoints/a1",
    ]
    assert result["records"][0]["summary"] == "親子丼 600 kcal（P 30g / C 80g）"
    assert result["records"][1]["summary"] == "サラダ 50 kcal"
    assert "LUNCH" in result["summary"]


async def test_log_water_records_milliliters():
    repository = FakeNutritionRepository()

    result = await HealthService(repository).log_water(500, date="2026-01-31", time="15:00")

    assert repository.calls[0][:2] == ("create_hydration", 500.0)
    assert result["record"]["name"] == "users/me/dataTypes/hydration-log/dataPoints/w1"
    assert result["logged_at"] == "2026-01-31T15:00"
    assert "500 ml" in result["summary"]


async def test_log_water_rejects_a_negative_amount():
    repository = FakeNutritionRepository()

    with pytest.raises(ValueError, match="amount_ml"):
        await HealthService(repository).log_water(-1)

    assert repository.calls == []


def test_summarize_nutrition_log_returns_totals_and_entries():
    result = HealthService.summarize_nutrition_log(
        "2026-01-31",
        {
            "nutrition-log": [
                _meal_point("…/a2", "サラダ", 13, kcal=50.0),
                _meal_point("…/a1", "親子丼", 12, kcal=600.0, protein=30.0, fat=15.0,
                            carbohydrate=80.0),
            ],
            "hydration-log": [
                {
                    "name": "…/w1",
                    "hydrationLog": {
                        "interval": {"civilStartTime": _civil(2026, 1, 31, hours=15)},
                        "amountConsumed": {"milliliters": 500.0},
                    },
                }
            ],
            "nutrition-log-rollup": [
                {
                    "nutritionLog": {
                        "energy": {"kcalSum": 650.0},
                        "nutrients": [{"nutrient": "PROTEIN", "quantity": {"gramsSum": 30.0}}],
                        "totalFat": {"gramsSum": 15.0},
                        "totalCarbohydrate": {"gramsSum": 80.0},
                    }
                }
            ],
            "hydration-log-rollup": [
                {"hydrationLog": {"amountConsumed": {"millilitersSum": 1500.0}}}
            ],
        },
    )

    assert result["totals"] == {
        "energy_kcal": 650.0,
        "protein_g": 30.0,
        "fat_g": 15.0,
        "carbohydrate_g": 80.0,
        "water_ml": 1500.0,
    }
    # 時刻の早い順に並べ、削除に使う name を残すこと
    assert [entry["food_name"] for entry in result["meals"]] == ["親子丼", "サラダ"]
    assert result["meals"][0]["name"] == "…/a1"
    assert result["meals"][0]["time"] == "2026-01-31T12:00"
    assert result["meals"][0]["protein_g"] == 30.0
    assert result["meals"][1]["protein_g"] is None
    assert result["hydration"] == [
        {"name": "…/w1", "time": "2026-01-31T15:00", "amount_ml": 500.0}
    ]


def test_summarize_nutrition_log_without_records():
    result = HealthService.summarize_nutrition_log("2026-01-31", {})

    assert result["meals"] == []
    assert result["hydration"] == []
    assert result["totals"] == {
        "energy_kcal": None,
        "protein_g": None,
        "fat_g": None,
        "carbohydrate_g": None,
        "water_ml": None,
    }


def test_summarize_devices_omits_the_mac_address():
    result = HealthService.summarize_devices(
        [
            {
                "deviceVersion": "Google Fitbit Air",
                "deviceType": "TRACKER",
                "batteryStatus": "High",
                "batteryLevel": 85,
                "lastSyncTime": "2026-01-31T08:00:00Z",
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "features": ["ACTIVE_MINUTES"],
            }
        ]
    )

    assert result["count"] == 1
    assert result["devices"] == [
        {
            "device_version": "Google Fitbit Air",
            "device_type": "TRACKER",
            "battery_status": "High",
            "battery_level": 85,
            "last_sync_time": "2026-01-31T08:00:00Z",
        }
    ]


def test_summarize_devices_without_devices():
    assert HealthService.summarize_devices([]) == {"devices": [], "count": 0}


async def test_get_nutrition_log_parses_the_date_and_summarizes():
    repository = FakeNutritionRepository(
        nutrition={
            "nutrition-log": [_meal_point("…/a1", "親子丼", 12, kcal=600.0)],
            "nutrition-log-rollup": [{"nutritionLog": {"energy": {"kcalSum": 600.0}}}],
        }
    )

    result = await HealthService(repository).get_nutrition_log("2026-01-31")

    assert repository.calls == [("nutrition_log", dt.date(2026, 1, 31))]
    assert result["date"] == "2026-01-31"
    assert result["totals"]["energy_kcal"] == 600.0
    assert [entry["food_name"] for entry in result["meals"]] == ["親子丼"]


async def test_get_nutrition_log_rejects_an_invalid_date_without_calling_the_api():
    repository = FakeNutritionRepository()

    with pytest.raises(ValueError, match="日付"):
        await HealthService(repository).get_nutrition_log("2026/01/31")

    assert repository.calls == []


async def test_get_devices_summarizes_the_paired_devices():
    repository = FakeNutritionRepository(
        devices=[{"deviceVersion": "Google Fitbit Air", "macAddress": "AA:BB:CC:DD:EE:FF"}]
    )

    result = await HealthService(repository).get_devices()

    assert repository.calls == [("paired_devices",)]
    assert result["count"] == 1
    assert result["devices"][0]["device_version"] == "Google Fitbit Air"
    assert "macAddress" not in result["devices"][0]


@pytest.mark.parametrize("date, time", [("2026/01/31", None), (None, "12時")])
async def test_log_meal_rejects_an_invalid_date_or_time_without_calling_the_api(date, time):
    repository = FakeNutritionRepository()

    with pytest.raises(ValueError):
        await HealthService(repository).log_meal(
            "LUNCH", [{"food_name": "丼", "energy_kcal": 600}], date=date, time=time
        )

    assert repository.calls == []


async def test_log_meal_tells_how_to_find_the_name_when_the_operation_is_unfinished():
    class UnfinishedRepository(FakeNutritionRepository):
        async def create_nutrition_logs(self, meal_type, items, start, end):
            self.calls.append(("create_nutrition", meal_type, items, start, end))
            # done が false の Operation では、作成された記録を特定できない
            return [{} for _ in items]

    result = await HealthService(UnfinishedRepository()).log_meal(
        "LUNCH", [{"food_name": "親子丼", "energy_kcal": 600}], date="2026-01-31", time="12:30"
    )

    assert result["records"][0]["name"] is None
    assert "get_nutrition_log" in result["records"][0]["summary"]
    # 記録そのものの要約は、外側の summary では変わらない
    assert "親子丼 600 kcal" in result["summary"]


def test_parse_meal_items_allows_zero():
    items = HealthService.parse_meal_items(
        [{"food_name": "水", "energy_kcal": 0, "protein_g": 0}]
    )

    assert items[0]["energy_kcal"] == 0.0
    assert items[0]["protein_g"] == 0.0


@pytest.mark.parametrize("date, time", [("2026/01/31", None), (None, "12時")])
async def test_log_water_rejects_an_invalid_date_or_time_without_calling_the_api(date, time):
    repository = FakeNutritionRepository()

    with pytest.raises(ValueError):
        await HealthService(repository).log_water(500, date=date, time=time)

    assert repository.calls == []


async def test_log_water_tells_how_to_find_the_name_when_the_operation_is_unfinished():
    class UnfinishedRepository(FakeNutritionRepository):
        async def create_hydration_log(self, amount_ml, start, end):
            self.calls.append(("create_hydration", amount_ml, start, end))
            return {}

    result = await HealthService(UnfinishedRepository()).log_water(
        500, date="2026-01-31", time="15:00"
    )

    assert result["record"]["name"] is None
    assert "get_nutrition_log" in result["record"]["summary"]
    assert result["summary"] == "2026-01-31T15:00 に 水分 500 ml を記録しました"


async def test_log_water_allows_zero():
    repository = FakeNutritionRepository()

    result = await HealthService(repository).log_water(0, date="2026-01-31", time="15:00")

    assert repository.calls[0][:2] == ("create_hydration", 0.0)
    assert "0 ml" in result["summary"]


def test_parse_log_interval_crosses_midnight_at_the_end_of_the_day():
    now = dt.datetime(2026, 1, 31, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))

    start, end = HealthService.parse_log_interval("2026-01-31", "23:59", now)

    assert start < end
    assert end == dt.datetime(2026, 2, 1, 0, 0, tzinfo=now.tzinfo)


async def test_delete_nutrition_logs_does_not_claim_success_before_the_operation_finishes():
    repository = FakeNutritionRepository(delete_completed=False)

    result = await HealthService(repository).delete_nutrition_logs(
        ["users/me/dataTypes/nutrition-log/dataPoints/a1"]
    )

    assert result["count"] == 1
    assert "削除しました" not in result["summary"]
    assert "get_nutrition_log" in result["summary"]


async def test_delete_nutrition_logs_says_deleted_when_the_operation_finished():
    result = await HealthService(FakeNutritionRepository()).delete_nutrition_logs(
        ["users/me/dataTypes/nutrition-log/dataPoints/a1"]
    )

    assert "削除しました" in result["summary"]
