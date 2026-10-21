from __future__ import annotations

import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo

from src.constants import (
    ACTIVITY_TREND_MAX_DAYS,
    BODY_MEASUREMENTS_MAX_DAYS,
    EXERCISE_HISTORY_MAX_DAYS,
    HEART_RATE_ELEVATED_ZONES,
    NUTRITION_LOG_INTERVAL_MINUTES,
    NUTRITION_LOG_TIME_ZONE,
    NUTRITION_MEAL_TYPES,
    NUTRITION_WRITABLE_DATA_TYPES,
)
from src.models import (
    ActivityTrend,
    ActivityTrendAverages,
    ActivityTrendDay,
    ActivityTrendTotals,
    BodyMeasurementDay,
    BodyMeasurements,
    DailyActivity,
    DeletedLogs,
    ExerciseHistory,
    ExerciseSession,
    HealthMetrics,
    HeartRateSummary,
    HydrationEntry,
    LoggedRecord,
    MainSleep,
    MealLog,
    MeasurementStats,
    NutritionEntry,
    NutritionLog,
    NutritionTotals,
    PairedDevice,
    PairedDevices,
    SleepLog,
    WaterLog,
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

    async def get_health_metrics(self, date: str) -> HealthMetrics:
        points = await self._repository.fetch_daily_metrics(self.parse_date(date))
        return self.summarize_health_metrics(date, points)

    async def get_body_measurements(self, start_date: str, end_date: str) -> BodyMeasurements:
        start, end = self.parse_range(start_date, end_date, BODY_MEASUREMENTS_MAX_DAYS)
        points = await self._repository.fetch_body_measurements(start, end)
        return self.summarize_body_measurements(start, end, points)

    async def get_activity_trend(self, start_date: str, end_date: str) -> ActivityTrend:
        start, end = self.parse_range(start_date, end_date, ACTIVITY_TREND_MAX_DAYS)
        rollups, resting = await self._repository.fetch_activity_trend(start, end)
        return self.summarize_activity_trend(start, end, rollups, resting)

    async def get_exercise_history(self, start_date: str, end_date: str) -> ExerciseHistory:
        start, end = self.parse_range(start_date, end_date, EXERCISE_HISTORY_MAX_DAYS)
        points = await self._repository.fetch_exercises(start, end)
        sessions = sorted(
            (self.summarize_exercise(point.get("exercise", {})) for point in points),
            key=lambda session: session["start"] or "",
        )
        return {"sessions": sessions, "count": len(sessions)}

    async def get_nutrition_log(self, date: str) -> NutritionLog:
        points = await self._repository.fetch_nutrition_log(self.parse_date(date))
        return self.summarize_nutrition_log(date, points)

    async def get_devices(self) -> PairedDevices:
        devices = await self._repository.fetch_paired_devices()
        return self.summarize_devices(devices)

    async def log_meal(
        self, meal_type: str, items: list[dict], date: str | None = None, time: str | None = None
    ) -> MealLog:
        validated_type = self.parse_meal_type(meal_type)
        validated_items = self.parse_meal_items(items)
        start, end = self.parse_log_interval(date, time, self._now())
        created = await self._repository.create_nutrition_logs(
            validated_type, validated_items, start, end
        )
        return self.summarize_meal_log(validated_type, validated_items, start, created)

    async def log_water(
        self, amount_ml: float, date: str | None = None, time: str | None = None
    ) -> WaterLog:
        amount = self.parse_amount_ml(amount_ml)
        start, end = self.parse_log_interval(date, time, self._now())
        created = await self._repository.create_hydration_log(amount, start, end)
        return self.summarize_water_log(amount, start, created)

    async def delete_nutrition_logs(self, names: list[str]) -> DeletedLogs:
        validated = self.parse_log_names(names)
        await self._repository.delete_nutrition_data_points(validated)
        return self.summarize_deleted_logs(validated)

    @staticmethod
    def _now() -> dt.datetime:
        return dt.datetime.now(ZoneInfo(NUTRITION_LOG_TIME_ZONE))

    @staticmethod
    def parse_date(value: str) -> dt.date:
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"日付は YYYY-MM-DD 形式で指定してください: {value!r}") from exc

    @staticmethod
    def parse_range(start_date: str, end_date: str, max_days: int) -> tuple[dt.date, dt.date]:
        start, end = HealthService.parse_date(start_date), HealthService.parse_date(end_date)
        if start > end:
            raise ValueError("start_date には end_date 以前の日付を指定してください")
        if (end - start).days + 1 > max_days:
            raise ValueError(
                f"期間は {max_days} 日以内で指定してください。"
                "それより長い期間は分けて呼び出してください"
            )
        return start, end

    @staticmethod
    def parse_time(value: str) -> dt.time:
        try:
            parsed = dt.time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"時刻は HH:MM 形式で指定してください: {value!r}") from exc
        # オフセット付きで渡されるとタイムゾーンの取り違えに気づけないので受け付けない
        if parsed.tzinfo is not None:
            raise ValueError(f"時刻は HH:MM 形式で指定してください: {value!r}")
        return parsed

    @staticmethod
    def parse_log_interval(
        date: str | None, time: str | None, now: dt.datetime
    ) -> tuple[dt.datetime, dt.datetime]:
        """記録する時刻の範囲。省略された日付・時刻は now で補う。

        API は startTime < endTime を要求するが食事・水分は時点の記録なので、終了は定数の幅だけ
        後ろに置く。日付ごとに UTC オフセットが変わりうるため、tzinfo は now から引き継ぐ。
        """
        day = HealthService.parse_date(date) if date is not None else now.date()
        clock = (
            HealthService.parse_time(time)
            if time is not None
            else now.time().replace(microsecond=0)
        )
        start = dt.datetime.combine(day, clock, tzinfo=now.tzinfo)
        return start, start + dt.timedelta(minutes=NUTRITION_LOG_INTERVAL_MINUTES)

    @staticmethod
    def parse_meal_type(value: str) -> str:
        meal_type = value.upper()
        if meal_type not in NUTRITION_MEAL_TYPES:
            raise ValueError(
                f"meal_type は {' / '.join(NUTRITION_MEAL_TYPES)} のいずれかを指定してください: "
                f"{value!r}"
            )
        return meal_type

    @staticmethod
    def parse_meal_items(items: list[dict]) -> list[dict]:
        if not items:
            raise ValueError("items には記録する品目を1つ以上指定してください")
        return [
            {
                "food_name": HealthService._parse_food_name(item.get("food_name")),
                "energy_kcal": HealthService._required_amount(
                    item.get("energy_kcal"), "energy_kcal"
                ),
                "protein_g": HealthService._optional_amount(item.get("protein_g"), "protein_g"),
                "fat_g": HealthService._optional_amount(item.get("fat_g"), "fat_g"),
                "carbohydrate_g": HealthService._optional_amount(
                    item.get("carbohydrate_g"), "carbohydrate_g"
                ),
            }
            for item in items
        ]

    @staticmethod
    def parse_amount_ml(value: float) -> float:
        return HealthService._required_amount(value, "amount_ml")

    @staticmethod
    def parse_log_names(names: list[str]) -> list[str]:
        """削除できるのは食事・水分の記録だけなので、ほかのデータ型は API を呼ぶ前に弾く。"""
        if not names:
            raise ValueError("names には削除する記録の name を1つ以上指定してください")
        for name in names:
            if HealthRepository.data_point_data_type(name) not in NUTRITION_WRITABLE_DATA_TYPES:
                raise ValueError(
                    f"削除できるのは {' と '.join(NUTRITION_WRITABLE_DATA_TYPES)} の記録だけです。"
                    f"get_nutrition_log が返した name を指定してください: {name!r}"
                )
        return list(names)

    @staticmethod
    def _parse_food_name(value: str | None) -> str:
        food_name = (value or "").strip()
        if not food_name:
            raise ValueError("items の各品目には food_name を指定してください")
        return food_name

    @staticmethod
    def _required_amount(value: float | None, field: str) -> float:
        if value is None:
            raise ValueError(f"{field} を指定してください")
        return HealthService._non_negative(value, field)

    @staticmethod
    def _optional_amount(value: float | None, field: str) -> float | None:
        return None if value is None else HealthService._non_negative(value, field)

    @staticmethod
    def _non_negative(value: float, field: str) -> float:
        if value < 0:
            raise ValueError(f"{field} には0以上の値を指定してください: {value!r}")
        return float(value)

    @staticmethod
    def summarize_activity(date: str, rollups: dict[str, list[dict]]) -> DailyActivity:
        steps = HealthService._first(rollups.get("steps")).get("steps", {})
        distance = HealthService._first(rollups.get("distance")).get("distance", {})
        calories = HealthService._first(rollups.get("total-calories")).get("totalCalories", {})
        active = HealthService._first(rollups.get("active-minutes")).get("activeMinutes", {})
        floors = HealthService._first(rollups.get("floors")).get("floors", {})
        zones = HealthService._first(rollups.get("active-zone-minutes")).get(
            "activeZoneMinutes", {}
        )
        millimeters = distance.get("millimetersSum")
        kcal = calories.get("kcalSum")
        by_level = HealthService._active_minutes_by_level(active)
        by_zone = HealthService._active_zone_minutes_by_zone(zones)
        return {
            "date": date,
            "steps": HealthService._to_int(steps.get("countSum")),
            "calories_out_kcal": round(kcal) if kcal is not None else None,
            "distance_km": (
                round(int(millimeters) / 1_000_000, 2) if millimeters is not None else None
            ),
            "active_minutes": by_level.get("moderate", 0) + by_level.get("vigorous", 0),
            "active_minutes_by_level": by_level,
            "floors": HealthService._to_int(floors.get("countSum")),
            "active_zone_minutes_by_zone": by_zone,
            "active_zone_minutes": sum(by_zone.values()),
        }

    @staticmethod
    def summarize_health_metrics(date: str, points: dict[str, list[dict]]) -> HealthMetrics:
        hrv = HealthService._first(points.get("daily-heart-rate-variability")).get(
            "dailyHeartRateVariability", {}
        )
        respiratory = HealthService._first(points.get("daily-respiratory-rate")).get(
            "dailyRespiratoryRate", {}
        )
        oxygen = HealthService._first(points.get("daily-oxygen-saturation")).get(
            "dailyOxygenSaturation", {}
        )
        temperature = HealthService._first(points.get("daily-sleep-temperature-derivations")).get(
            "dailySleepTemperatureDerivations", {}
        )
        vo2_max = HealthService._first(points.get("daily-vo2-max")).get("dailyVo2Max", {})
        nightly = temperature.get("nightlyTemperatureCelsius")
        baseline = temperature.get("baselineTemperatureCelsius")
        return {
            "date": date,
            "heart_rate_variability": (
                {
                    "average_milliseconds": HealthService._rounded(
                        hrv.get("averageHeartRateVariabilityMilliseconds"), 1
                    ),
                    "deep_sleep_rmssd_milliseconds": HealthService._rounded(
                        hrv.get("deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds"), 1
                    ),
                    "non_rem_heart_rate": HealthService._to_int(
                        hrv.get("nonRemHeartRateBeatsPerMinute")
                    ),
                }
                if hrv
                else None
            ),
            "respiratory_rate_breaths_per_minute": HealthService._rounded(
                respiratory.get("breathsPerMinute"), 1
            ),
            "oxygen_saturation": (
                {
                    "average_percentage": HealthService._rounded(
                        oxygen.get("averagePercentage"), 1
                    ),
                    "lower_bound_percentage": HealthService._rounded(
                        oxygen.get("lowerBoundPercentage"), 1
                    ),
                    "upper_bound_percentage": HealthService._rounded(
                        oxygen.get("upperBoundPercentage"), 1
                    ),
                }
                if oxygen
                else None
            ),
            "skin_temperature": (
                {
                    "nightly_celsius": HealthService._rounded(nightly, 2),
                    "baseline_celsius": HealthService._rounded(baseline, 2),
                    # API は基準値との差を返さないため、夜間の値 - 基準値で算出する
                    "difference_celsius": (
                        round(nightly - baseline, 2)
                        if nightly is not None and baseline is not None
                        else None
                    ),
                }
                if temperature
                else None
            ),
            "vo2_max": (
                {
                    "vo2_max": HealthService._rounded(vo2_max.get("vo2Max"), 1),
                    "cardio_fitness_level": vo2_max.get("cardioFitnessLevel"),
                    # proto3 JSON は値 false を省略する。vo2Max が返っている記録で
                    # estimated が欠けていれば「推定ではない」の意味になる
                    "estimated": vo2_max.get("estimated", False),
                }
                if vo2_max
                else None
            ),
        }

    @staticmethod
    def summarize_body_measurements(
        start: dt.date, end: dt.date, points: dict[str, list[dict]]
    ) -> BodyMeasurements:
        weight_kg = HealthService._in_range(
            {
                date: grams / 1000
                for date, grams in HealthService._daily_last_sample(
                    points.get("weight"), "weight", "weightGrams"
                ).items()
            },
            start,
            end,
        )
        body_fat = HealthService._in_range(
            HealthService._daily_last_sample(points.get("body-fat"), "bodyFat", "percentage"),
            start,
            end,
        )
        days: list[BodyMeasurementDay] = [
            {
                "date": date,
                "weight_kg": HealthService._rounded(weight_kg.get(date), 2),
                "body_fat_percentage": HealthService._rounded(body_fat.get(date), 1),
            }
            for date in sorted(weight_kg.keys() | body_fat.keys())
        ]
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "weight_kg": HealthService._measurement_stats(weight_kg, 2),
            "body_fat_percentage": HealthService._measurement_stats(body_fat, 1),
            "days": days,
        }

    @staticmethod
    def summarize_activity_trend(
        start: dt.date, end: dt.date, rollups: dict[str, list[dict]], resting: list[dict]
    ) -> ActivityTrend:
        steps = HealthService._rollup_by_date(rollups.get("steps"), "steps")
        distance = HealthService._rollup_by_date(rollups.get("distance"), "distance")
        calories = HealthService._rollup_by_date(rollups.get("total-calories"), "totalCalories")
        active = HealthService._rollup_by_date(rollups.get("active-minutes"), "activeMinutes")
        floors = HealthService._rollup_by_date(rollups.get("floors"), "floors")
        zones = HealthService._rollup_by_date(
            rollups.get("active-zone-minutes"), "activeZoneMinutes"
        )
        resting_by_date = HealthService._resting_heart_rate_by_date(resting)

        days: list[ActivityTrendDay] = []
        for day in HealthService._each_date(start, end):
            date = day.isoformat()
            millimeters = distance.get(date, {}).get("millimetersSum")
            kcal = calories.get(date, {}).get("kcalSum")
            by_level = HealthService._active_minutes_by_level(active.get(date, {}))
            by_zone = HealthService._active_zone_minutes_by_zone(zones.get(date, {}))
            days.append(
                {
                    "date": date,
                    "steps": HealthService._to_int(steps.get(date, {}).get("countSum")),
                    "distance_km": (
                        round(int(millimeters) / 1_000_000, 2) if millimeters is not None else None
                    ),
                    "calories_out_kcal": round(kcal) if kcal is not None else None,
                    "active_minutes": (
                        by_level.get("moderate", 0) + by_level.get("vigorous", 0)
                        if by_level
                        else None
                    ),
                    "floors": HealthService._to_int(floors.get(date, {}).get("countSum")),
                    "active_zone_minutes": sum(by_zone.values()) if by_zone else None,
                    "resting_heart_rate": resting_by_date.get(date),
                }
            )

        totals: ActivityTrendTotals = {
            "steps": HealthService._sum(days, "steps"),
            "distance_km": HealthService._sum(days, "distance_km", 2),
            "calories_out_kcal": HealthService._sum(days, "calories_out_kcal"),
            "active_minutes": HealthService._sum(days, "active_minutes"),
            "floors": HealthService._sum(days, "floors"),
            "active_zone_minutes": HealthService._sum(days, "active_zone_minutes"),
        }
        averages: ActivityTrendAverages = {
            "steps": HealthService._average(days, "steps", 1),
            "distance_km": HealthService._average(days, "distance_km", 2),
            "calories_out_kcal": HealthService._average(days, "calories_out_kcal", 1),
            "active_minutes": HealthService._average(days, "active_minutes", 1),
            "floors": HealthService._average(days, "floors", 1),
            "active_zone_minutes": HealthService._average(days, "active_zone_minutes", 1),
            "resting_heart_rate": HealthService._average(days, "resting_heart_rate", 1),
        }
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": days,
            "totals": totals,
            "averages": averages,
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
            "start": HealthService._local_datetime(interval, "start"),
            "end": HealthService._local_datetime(interval, "end"),
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
        zone_minutes = {
            name.lower(): HealthService._duration_minutes(zone.get("duration")) or 0
            for zone in zone_values.get("timeInHeartRateZones", [])
            if (name := zone.get("heartRateZone", "")) in HEART_RATE_ELEVATED_ZONES
        }
        return {
            "date": date,
            "resting_heart_rate": HealthService._to_int(resting_value.get("beatsPerMinute")),
            "heart_rate_zone_minutes": zone_minutes,
            "elevated_heart_rate_minutes": sum(zone_minutes.values()),
        }

    @staticmethod
    def summarize_exercise(exercise: dict) -> ExerciseSession:
        metrics = exercise.get("metricsSummary", {})
        interval = exercise.get("interval", {})
        distance_mm = metrics.get("distanceMillimeters")
        calories = metrics.get("caloriesKcal")
        average_heart_rate = HealthService._to_int(metrics.get("averageHeartRateBeatsPerMinute"))
        # proto3 JSON は値0のフィールドを省略する。アクティブゾーン分数は心拍から算出されるので、
        # 心拍があるのに欠けていれば0分、心拍もなければ未計測として None にする
        active_zone_minutes = HealthService._to_int(metrics.get("activeZoneMinutes"))
        if active_zone_minutes is None and average_heart_rate is not None:
            active_zone_minutes = 0
        return {
            "name": exercise.get("displayName"),
            "type": exercise.get("exerciseType"),
            "start": HealthService._local_datetime(interval, "start"),
            "end": HealthService._local_datetime(interval, "end"),
            "active_minutes": HealthService._duration_minutes(exercise.get("activeDuration")),
            "calories_kcal": round(calories) if calories is not None else None,
            "average_heart_rate": average_heart_rate,
            "distance_km": round(distance_mm / 1_000_000, 2) if distance_mm is not None else None,
            "steps": HealthService._to_int(metrics.get("steps")),
            "active_zone_minutes": active_zone_minutes,
        }

    @staticmethod
    def summarize_nutrition_log(date: str, points: dict[str, list[dict]]) -> NutritionLog:
        meal_totals = HealthService._first(points.get("nutrition-log-rollup")).get(
            "nutritionLog", {}
        )
        water_totals = HealthService._first(points.get("hydration-log-rollup")).get(
            "hydrationLog", {}
        )
        totals: NutritionTotals = {
            "energy_kcal": HealthService._rounded(meal_totals.get("energy", {}).get("kcalSum"), 1),
            "protein_g": HealthService._rounded(
                HealthService._nutrient_amount(meal_totals, "PROTEIN", "gramsSum"), 1
            ),
            "fat_g": HealthService._rounded(meal_totals.get("totalFat", {}).get("gramsSum"), 1),
            "carbohydrate_g": HealthService._rounded(
                meal_totals.get("totalCarbohydrate", {}).get("gramsSum"), 1
            ),
            "water_ml": HealthService._rounded(
                water_totals.get("amountConsumed", {}).get("millilitersSum"), 1
            ),
        }
        meals = sorted(
            (
                HealthService._nutrition_entry(point)
                for point in points.get("nutrition-log", [])
                if "nutritionLog" in point
            ),
            key=lambda entry: entry["time"] or "",
        )
        hydration = sorted(
            (
                HealthService._hydration_entry(point)
                for point in points.get("hydration-log", [])
                if "hydrationLog" in point
            ),
            key=lambda entry: entry["time"] or "",
        )
        return {"date": date, "totals": totals, "meals": meals, "hydration": hydration}

    @staticmethod
    def summarize_devices(devices: list[dict]) -> PairedDevices:
        summarized: list[PairedDevice] = [
            {
                "device_version": device.get("deviceVersion"),
                "device_type": device.get("deviceType"),
                "battery_status": device.get("batteryStatus"),
                "battery_level": HealthService._to_int(device.get("batteryLevel")),
                "last_sync_time": device.get("lastSyncTime"),
            }
            # macAddress は端末を一意に識別できるうえ利用者の判断に要らないので返さない
            for device in devices
        ]
        return {"devices": summarized, "count": len(summarized)}

    @staticmethod
    def summarize_meal_log(
        meal_type: str, items: list[dict], start: dt.datetime, created: list[dict]
    ) -> MealLog:
        item_summaries = [HealthService._meal_item_summary(item) for item in items]
        records: list[LoggedRecord] = [
            HealthService._logged_record(point.get("name"), summary)
            for summary, point in zip(item_summaries, created, strict=True)
        ]
        total = sum(item["energy_kcal"] for item in items)
        logged_at = HealthService._logged_at(start)
        return {
            "meal_type": meal_type,
            "logged_at": logged_at,
            "records": records,
            "total_energy_kcal": round(total, 1),
            "summary": (
                f"{logged_at} の {meal_type} として "
                f"{'、'.join(item_summaries)} "
                f"（計 {HealthService._format_amount(total)} kcal）を記録しました"
            ),
        }

    @staticmethod
    def summarize_water_log(amount_ml: float, start: dt.datetime, created: dict) -> WaterLog:
        logged_at = HealthService._logged_at(start)
        summary = f"水分 {HealthService._format_amount(amount_ml)} ml"
        return {
            "logged_at": logged_at,
            "record": HealthService._logged_record(created.get("name"), summary),
            "summary": f"{logged_at} に {summary} を記録しました",
        }

    @staticmethod
    def summarize_deleted_logs(names: list[str]) -> DeletedLogs:
        return {
            "names": names,
            "count": len(names),
            "summary": f"食事・水分の記録を {len(names)} 件削除しました",
        }

    @staticmethod
    def _logged_record(name: str | None, summary: str) -> LoggedRecord:
        """記録は作成されても、Operation が完了していないと name が返らない。

        その場合は削除に使える識別子が無いので、要約で調べ直し方を伝える。
        """
        if name is None:
            return {
                "name": None,
                "summary": f"{summary}（name は未確定。削除するには get_nutrition_log で調べる）",
            }
        return {"name": name, "summary": summary}

    @staticmethod
    def _nutrition_entry(point: dict) -> NutritionEntry:
        log = point.get("nutritionLog", {})
        return {
            "name": point.get("name"),
            "food_name": log.get("foodDisplayName"),
            "meal_type": log.get("mealType"),
            "time": HealthService._local_datetime(log.get("interval", {}), "start"),
            "energy_kcal": HealthService._rounded(log.get("energy", {}).get("kcal"), 1),
            "protein_g": HealthService._rounded(
                HealthService._nutrient_amount(log, "PROTEIN", "grams"), 1
            ),
            "fat_g": HealthService._rounded(log.get("totalFat", {}).get("grams"), 1),
            "carbohydrate_g": HealthService._rounded(
                log.get("totalCarbohydrate", {}).get("grams"), 1
            ),
        }

    @staticmethod
    def _hydration_entry(point: dict) -> HydrationEntry:
        log = point.get("hydrationLog", {})
        return {
            "name": point.get("name"),
            "time": HealthService._local_datetime(log.get("interval", {}), "start"),
            "amount_ml": HealthService._rounded(
                log.get("amountConsumed", {}).get("milliliters"), 1
            ),
        }

    @staticmethod
    def _nutrient_amount(log: dict, nutrient: str, key: str) -> float | None:
        """nutrients は栄養素ごとの配列。記録では key が grams、日次集計では gramsSum になる。"""
        for entry in log.get("nutrients", []):
            if entry.get("nutrient") == nutrient:
                return entry.get("quantity", {}).get(key)
        return None

    @staticmethod
    def _meal_item_summary(item: dict) -> str:
        macros = [
            f"{label} {HealthService._format_amount(value)}g"
            for label, value in (
                ("P", item["protein_g"]),
                ("F", item["fat_g"]),
                ("C", item["carbohydrate_g"]),
            )
            if value is not None
        ]
        summary = f"{item['food_name']} {HealthService._format_amount(item['energy_kcal'])} kcal"
        return f"{summary}（{' / '.join(macros)}）" if macros else summary

    @staticmethod
    def _format_amount(value: float) -> str:
        """要約の文言用。600.0 のように小数部のない値は整数で書く。"""
        rounded = round(value, 1)
        return str(int(rounded) if rounded.is_integer() else rounded)

    @staticmethod
    def _logged_at(start: dt.datetime) -> str:
        return start.strftime("%Y-%m-%dT%H:%M")

    @staticmethod
    def _first(points: list[dict] | None) -> dict:
        return points[0] if points else {}

    @staticmethod
    def _active_minutes_by_level(rollup: dict) -> dict[str, int]:
        return {
            entry.get("activityLevel", "").lower(): int(entry.get("activeMinutesSum", 0))
            for entry in rollup.get("activeMinutesRollupByActivityLevel", [])
        }

    @staticmethod
    def _active_zone_minutes_by_zone(rollup: dict) -> dict[str, int]:
        """アクティブゾーン時間の3ゾーンのうち、値が返ったゾーンだけを返す。"""
        zones = {
            "fat_burn": rollup.get("sumInFatBurnHeartZone"),
            "cardio": rollup.get("sumInCardioHeartZone"),
            "peak": rollup.get("sumInPeakHeartZone"),
        }
        return {zone: int(value) for zone, value in zones.items() if value is not None}

    @staticmethod
    def _rollup_by_date(points: list[dict] | None, field: str) -> dict[str, dict]:
        """日次集計の枠を、枠の開始日（"YYYY-MM-DD"）で引けるようにする。"""
        by_date: dict[str, dict] = {}
        for point in points or []:
            date = HealthService._format_date(point.get("civilStartTime", {}).get("date"))
            if date is not None:
                by_date[date] = point.get(field, {})
        return by_date

    @staticmethod
    def _resting_heart_rate_by_date(points: list[dict]) -> dict[str, int]:
        by_date: dict[str, int] = {}
        for point in points:
            value = point.get("dailyRestingHeartRate", {})
            date = HealthService._format_date(value.get("date"))
            beats = HealthService._to_int(value.get("beatsPerMinute"))
            if date is not None and beats is not None:
                by_date[date] = beats
        return by_date

    @staticmethod
    def _daily_last_sample(
        points: list[dict] | None, field: str, value_key: str
    ) -> dict[str, float]:
        """日ごとにその日の最後の記録の値を返す。1日に複数回記録されるため1件に絞る。"""
        latest: dict[str, tuple[str, float]] = {}
        for point in points or []:
            sample = point.get(field, {})
            value = sample.get(value_key)
            sample_time = sample.get("sampleTime", {})
            date = HealthService._sample_date(sample_time)
            if value is None or date is None:
                continue
            # Timestamp の JSON 表現は固定長の UTC 文字列なので、秒までを切り出せば文字列同士の
            # 比較で前後が決まる
            physical_time = sample_time.get("physicalTime", "")[:19]
            previous = latest.get(date)
            if previous is None or previous[0] <= physical_time:
                latest[date] = (physical_time, float(value))
        return {date: value for date, (_, value) in latest.items()}

    @staticmethod
    def _in_range(
        values_by_date: dict[str, float], start: dt.date, end: dt.date
    ) -> dict[str, float]:
        """絞り込みは現地時刻の filter で API に任せる一方、記録日は physicalTime から算出する
        ため、境界日で両者がずれても期間外の日を返さないようにする。
        """
        first, last = start.isoformat(), end.isoformat()
        return {date: value for date, value in values_by_date.items() if first <= date <= last}

    @staticmethod
    def _measurement_stats(values_by_date: dict[str, float], digits: int) -> MeasurementStats:
        values = [values_by_date[date] for date in sorted(values_by_date)]
        if not values:
            return {
                "first": None,
                "last": None,
                "min": None,
                "max": None,
                "average": None,
                "count": 0,
            }
        return {
            "first": round(values[0], digits),
            "last": round(values[-1], digits),
            "min": round(min(values), digits),
            "max": round(max(values), digits),
            "average": round(sum(values) / len(values), digits),
            "count": len(values),
        }

    @staticmethod
    def _each_date(start: dt.date, end: dt.date) -> list[dt.date]:
        """start・end の両日を含む連続した日付。"""
        return [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]

    @staticmethod
    def _day_values(days: list[ActivityTrendDay], key: str) -> list[float]:
        return [value for day in days if (value := day.get(key)) is not None]

    @staticmethod
    def _sum(days: list[ActivityTrendDay], key: str, digits: int = 0) -> float | None:
        values = HealthService._day_values(days, key)
        if not values:
            return None
        return round(sum(values), digits) if digits else round(sum(values))

    @staticmethod
    def _average(days: list[ActivityTrendDay], key: str, digits: int) -> float | None:
        values = HealthService._day_values(days, key)
        return round(sum(values) / len(values), digits) if values else None

    @staticmethod
    def _to_int(value: str | int | None) -> int | None:
        return int(value) if value is not None else None

    @staticmethod
    def _rounded(value: float | None, digits: int) -> float | None:
        return round(value, digits) if value is not None else None

    @staticmethod
    def _duration_minutes(value: str | None) -> int | None:
        """google.protobuf.Duration の JSON 表現（例: "3600s", "90.5s"）を分に変換する。"""
        if not value:
            return None
        return round(float(value.rstrip("s")) / 60)

    @staticmethod
    def _local_datetime(interval: dict, edge: Literal["start", "end"]) -> str | None:
        """SessionTimeInterval の開始・終了を現地時刻の "YYYY-MM-DDTHH:MM" で返す。

        ディスカバリドキュメント上 civilStartTime / civilEndTime は Output only で、実際の list
        の応答には含まれない。必須の startTime / endTime（UTC）と startUtcOffset / endUtcOffset
        から現地時刻を算出し、civil 側が返ってきたときはそちらを使う。
        """
        civil = HealthService._format_civil_datetime(interval.get(f"civil{edge.title()}Time"))
        if civil is not None:
            return civil
        timestamp = interval.get(f"{edge}Time")
        if not timestamp:
            return None
        # Timestamp の JSON 表現は常に Z（UTC）で、秒の小数部は0〜9桁。分までしか使わないので
        # 秒までを切り出して解釈する
        utc = dt.datetime.strptime(timestamp[:19], "%Y-%m-%dT%H:%M:%S")
        offset = interval.get(f"{edge}UtcOffset") or "0s"
        local = utc + dt.timedelta(seconds=float(offset.rstrip("s")))
        return local.strftime("%Y-%m-%dT%H:%M")

    @staticmethod
    def _sample_date(sample_time: dict) -> str | None:
        """ObservationSampleTime の記録日を現地時刻の "YYYY-MM-DD" で返す。

        ディスカバリドキュメント上 civilTime は Output only で、実際の list の応答には含まれない
        ことがある。必須の physicalTime（UTC）と utcOffset から算出し、civilTime が返ってきた
        ときはそちらを使う。
        """
        civil = HealthService._format_civil_datetime(sample_time.get("civilTime"))
        if civil is not None:
            return civil[:10]
        physical_time = sample_time.get("physicalTime")
        if not physical_time:
            return None
        utc = dt.datetime.strptime(physical_time[:19], "%Y-%m-%dT%H:%M:%S")
        offset = sample_time.get("utcOffset") or "0s"
        return (utc + dt.timedelta(seconds=float(offset.rstrip("s")))).date().isoformat()

    @staticmethod
    def _format_civil_datetime(civil: dict | None) -> str | None:
        """CivilDateTime を "YYYY-MM-DDTHH:MM" に整形する。

        proto3 の JSON 表現では値0のフィールドが省略されるため、欠けていれば0として扱う。
        """
        if not civil or "date" not in civil:
            return None
        time = civil.get("time", {})
        return (
            f"{HealthService._format_date(civil['date'])}"
            f"T{time.get('hours', 0):02d}:{time.get('minutes', 0):02d}"
        )

    @staticmethod
    def _format_date(date: dict | None) -> str | None:
        """google.type.Date を "YYYY-MM-DD" に整形する。"""
        if not date:
            return None
        return f"{date.get('year', 0):04d}-{date.get('month', 0):02d}-{date.get('day', 0):02d}"
