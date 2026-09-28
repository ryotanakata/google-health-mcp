from __future__ import annotations

import datetime as dt

from src.constants import EXERCISE_HISTORY_MAX_DAYS
from src.models import (
    DailyActivity,
    ExerciseHistory,
    ExerciseSession,
    HeartRateSummary,
    MainSleep,
    SleepLog,
)
from src.repository import HealthRepository


class HealthService:
    def __init__(self, repository: HealthRepository) -> None:
        self._repository = repository

    async def get_daily_activity(self, date: str) -> DailyActivity:
        rollups = await self._repository.fetch_activity_rollups(self.parse_date(date))
        return self.summarize_activity(date, rollups)

    async def get_sleep_log(self, date: str) -> SleepLog:
        points = await self._repository.fetch_sleep_sessions(self.parse_date(date))
        return self.summarize_sleep(date, points)

    async def get_heart_rate_summary(self, date: str) -> HeartRateSummary:
        resting, zones = await self._repository.fetch_heart_rate(self.parse_date(date))
        return self.summarize_heart_rate(date, resting, zones)

    async def get_exercise_history(self, start_date: str, end_date: str) -> ExerciseHistory:
        start, end = self.parse_date(start_date), self.parse_date(end_date)
        if start > end:
            raise ValueError("start_date には end_date 以前の日付を指定してください")
        if (end - start).days + 1 > EXERCISE_HISTORY_MAX_DAYS:
            raise ValueError(
                f"期間は {EXERCISE_HISTORY_MAX_DAYS} 日以内で指定してください。"
                "それより長い期間は分けて呼び出してください"
            )
        points = await self._repository.fetch_exercises(start, end)
        sessions = sorted(
            (self.summarize_exercise(point.get("exercise", {})) for point in points),
            key=lambda session: session["start"] or "",
        )
        return {"sessions": sessions, "count": len(sessions)}

    @staticmethod
    def parse_date(value: str) -> dt.date:
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"日付は YYYY-MM-DD 形式で指定してください: {value!r}") from exc

    @staticmethod
    def summarize_activity(date: str, rollups: dict[str, list[dict]]) -> DailyActivity:
        steps = HealthService._first(rollups.get("steps")).get("steps", {})
        distance = HealthService._first(rollups.get("distance")).get("distance", {})
        calories = HealthService._first(rollups.get("total-calories")).get("totalCalories", {})
        active = HealthService._first(rollups.get("active-minutes")).get("activeMinutes", {})
        millimeters = distance.get("millimetersSum")
        kcal = calories.get("kcalSum")
        by_level = {
            entry.get("activityLevel", "").lower(): int(entry.get("activeMinutesSum", 0))
            for entry in active.get("activeMinutesRollupByActivityLevel", [])
        }
        return {
            "date": date,
            "steps": HealthService._to_int(steps.get("countSum")),
            "calories_out_kcal": round(kcal) if kcal is not None else None,
            "distance_km": (
                round(int(millimeters) / 1_000_000, 2) if millimeters is not None else None
            ),
            "active_minutes": by_level.get("moderate", 0) + by_level.get("vigorous", 0),
            "active_minutes_by_level": by_level,
        }

    @staticmethod
    def summarize_sleep(date: str, points: list[dict]) -> SleepLog:
        sessions = [point["sleep"] for point in points if "sleep" in point]
        if not sessions:
            return {"date": date, "total_minutes_asleep": 0, "main_sleep": None, "naps": 0}

        def minutes_asleep(session: dict) -> int:
            return int(session.get("summary", {}).get("minutesAsleep", 0))

        main = next(
            (s for s in sessions if s.get("metadata", {}).get("mainSleep")),
            max(sessions, key=minutes_asleep),
        )
        summary = main.get("summary", {})
        asleep = minutes_asleep(main)
        in_period = int(summary.get("minutesInSleepPeriod", 0))
        interval = main.get("interval", {})
        main_sleep: MainSleep = {
            "start": HealthService._format_civil_datetime(interval.get("civilStartTime")),
            "end": HealthService._format_civil_datetime(interval.get("civilEndTime")),
            "minutes_asleep": asleep,
            "minutes_in_sleep_period": in_period,
            # API は睡眠効率を返さないため、Fitbit と同じく「睡眠時間 / 就床時間」で算出する
            "efficiency": round(asleep / in_period * 100) if in_period else None,
            "stages_minutes": {
                stage.get("type", "").lower(): int(stage.get("minutes", 0))
                for stage in summary.get("stagesSummary", [])
            },
        }
        return {
            "date": date,
            "total_minutes_asleep": sum(minutes_asleep(s) for s in sessions),
            "main_sleep": main_sleep,
            "naps": sum(1 for s in sessions if s.get("metadata", {}).get("nap")),
        }

    @staticmethod
    def summarize_heart_rate(date: str, resting: list[dict], zones: list[dict]) -> HeartRateSummary:
        resting_value = HealthService._first(resting).get("dailyRestingHeartRate", {})
        zone_values = HealthService._first(zones).get("timeInHeartRateZone", {})
        return {
            "date": date,
            "resting_heart_rate": HealthService._to_int(resting_value.get("beatsPerMinute")),
            "heart_rate_zone_minutes": {
                zone.get("heartRateZone", "").lower(): (
                    HealthService._duration_minutes(zone.get("duration")) or 0
                )
                for zone in zone_values.get("timeInHeartRateZones", [])
            },
        }

    @staticmethod
    def summarize_exercise(exercise: dict) -> ExerciseSession:
        metrics = exercise.get("metricsSummary", {})
        interval = exercise.get("interval", {})
        distance_mm = metrics.get("distanceMillimeters")
        calories = metrics.get("caloriesKcal")
        return {
            "name": exercise.get("displayName"),
            "type": exercise.get("exerciseType"),
            "start": HealthService._format_civil_datetime(interval.get("civilStartTime")),
            "end": HealthService._format_civil_datetime(interval.get("civilEndTime")),
            "active_minutes": HealthService._duration_minutes(exercise.get("activeDuration")),
            "calories_kcal": round(calories) if calories is not None else None,
            "average_heart_rate": HealthService._to_int(
                metrics.get("averageHeartRateBeatsPerMinute")
            ),
            "distance_km": round(distance_mm / 1_000_000, 2) if distance_mm is not None else None,
            "steps": HealthService._to_int(metrics.get("steps")),
            "active_zone_minutes": HealthService._to_int(metrics.get("activeZoneMinutes")),
        }

    @staticmethod
    def _first(points: list[dict] | None) -> dict:
        return points[0] if points else {}

    @staticmethod
    def _to_int(value: str | int | None) -> int | None:
        return int(value) if value is not None else None

    @staticmethod
    def _duration_minutes(value: str | None) -> int | None:
        """google.protobuf.Duration の JSON 表現（例: "3600s", "90.5s"）を分に変換する。"""
        if not value:
            return None
        return round(float(value.rstrip("s")) / 60)

    @staticmethod
    def _format_civil_datetime(civil: dict | None) -> str | None:
        """CivilDateTime を "YYYY-MM-DDTHH:MM" に整形する。

        proto3 の JSON 表現では値0のフィールドが省略されるため、欠けていれば0として扱う。
        """
        if not civil or "date" not in civil:
            return None
        date = civil["date"]
        time = civil.get("time", {})
        return (
            f"{date.get('year', 0):04d}-{date.get('month', 0):02d}-{date.get('day', 0):02d}"
            f"T{time.get('hours', 0):02d}:{time.get('minutes', 0):02d}"
        )
