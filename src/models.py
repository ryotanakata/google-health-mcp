"""pydantic は Python 3.12 未満で typing.TypedDict を受け付けないため typing_extensions を使う。"""

from __future__ import annotations

from typing import NotRequired

from typing_extensions import TypedDict


class DailyActivity(TypedDict):
    date: str
    steps: int | None
    calories_out_kcal: int | None
    distance_km: float | None
    # Fitbit の「アクティブな時間」に相当する中強度以上（moderate + vigorous）の合計
    active_minutes: int
    active_minutes_by_level: dict[str, int]
    floors: int | None
    # fat_burn / cardio / peak のみ。実時間の分数ではなく、cardio と peak では1分あたり2が
    # 加算された値（HeartRateSummary のゾーン滞在時間とは別の指標）
    active_zone_minutes_by_zone: dict[str, int]
    active_zone_minutes: int


class MainSleep(TypedDict):
    start: str | None
    end: str | None
    minutes_asleep: int
    minutes_in_sleep_period: int
    efficiency: int | None
    stages_minutes: dict[str, int]


class SleepLog(TypedDict):
    date: str
    total_minutes_asleep: int
    main_sleep: MainSleep | None
    naps: int


class HeartRateSummary(TypedDict):
    date: str
    resting_heart_rate: int | None
    # moderate / vigorous / peak のみ。light は1日の残り時間なので含めない
    heart_rate_zone_minutes: dict[str, int]
    elevated_heart_rate_minutes: int


class ExerciseSession(TypedDict):
    name: str | None
    type: str | None
    start: str | None
    end: str | None
    active_minutes: int | None
    calories_kcal: int | None
    average_heart_rate: int | None
    distance_km: float | None
    steps: int | None
    active_zone_minutes: int | None


class ExerciseHistory(TypedDict):
    sessions: list[ExerciseSession]
    count: int


class HeartRateVariability(TypedDict):
    average_milliseconds: float | None
    deep_sleep_rmssd_milliseconds: float | None
    non_rem_heart_rate: int | None


class OxygenSaturation(TypedDict):
    average_percentage: float | None
    lower_bound_percentage: float | None
    upper_bound_percentage: float | None


class SkinTemperature(TypedDict):
    nightly_celsius: float | None
    baseline_celsius: float | None
    difference_celsius: float | None


class Vo2Max(TypedDict):
    vo2_max: float | None
    cardio_fitness_level: str | None
    estimated: bool | None


class HealthMetrics(TypedDict):
    date: str
    heart_rate_variability: HeartRateVariability | None
    respiratory_rate_breaths_per_minute: float | None
    oxygen_saturation: OxygenSaturation | None
    skin_temperature: SkinTemperature | None
    vo2_max: Vo2Max | None


class MeasurementStats(TypedDict):
    first: float | None
    last: float | None
    min: float | None
    max: float | None
    average: float | None
    # 記録のあった日数（1日1件に絞ったあとの件数）
    count: int


class BodyMeasurementDay(TypedDict):
    date: str
    weight_kg: float | None
    body_fat_percentage: float | None


class BodyMeasurements(TypedDict):
    start_date: str
    end_date: str
    weight_kg: MeasurementStats
    body_fat_percentage: MeasurementStats
    days: list[BodyMeasurementDay]


class ActivityTrendDay(TypedDict):
    date: str
    steps: int | None
    distance_km: float | None
    calories_out_kcal: int | None
    active_minutes: int | None
    floors: int | None
    active_zone_minutes: int | None
    resting_heart_rate: int | None


class ActivityTrendTotals(TypedDict):
    steps: int | None
    distance_km: float | None
    calories_out_kcal: int | None
    active_minutes: int | None
    floors: int | None
    active_zone_minutes: int | None


class ActivityTrendAverages(TypedDict):
    steps: float | None
    distance_km: float | None
    calories_out_kcal: float | None
    active_minutes: float | None
    floors: float | None
    active_zone_minutes: float | None
    resting_heart_rate: float | None


class ActivityTrend(TypedDict):
    start_date: str
    end_date: str
    days: list[ActivityTrendDay]
    totals: ActivityTrendTotals
    # データのない日を除いた平均
    averages: ActivityTrendAverages


class MealItem(TypedDict):
    """log_meal が受け取る1品目。PFC は分かっている分だけ渡せばよい。"""

    food_name: str
    energy_kcal: float
    protein_g: NotRequired[float | None]
    fat_g: NotRequired[float | None]
    carbohydrate_g: NotRequired[float | None]


class NutritionEntry(TypedDict):
    # delete_nutrition_logs に渡すデータポイントの識別子
    name: str | None
    food_name: str | None
    meal_type: str | None
    time: str | None
    energy_kcal: float | None
    protein_g: float | None
    fat_g: float | None
    carbohydrate_g: float | None


class HydrationEntry(TypedDict):
    name: str | None
    time: str | None
    amount_ml: float | None


class NutritionTotals(TypedDict):
    energy_kcal: float | None
    protein_g: float | None
    fat_g: float | None
    carbohydrate_g: float | None
    water_ml: float | None


class NutritionLog(TypedDict):
    date: str
    totals: NutritionTotals
    meals: list[NutritionEntry]
    hydration: list[HydrationEntry]


class LoggedRecord(TypedDict):
    # create は Operation を返す。完了前（done=false）は作成された記録を特定できず None になる
    name: str | None
    summary: str


class MealLog(TypedDict):
    meal_type: str
    logged_at: str
    records: list[LoggedRecord]
    total_energy_kcal: float
    summary: str


class WaterLog(TypedDict):
    logged_at: str
    record: LoggedRecord
    summary: str


class DeletedLogs(TypedDict):
    names: list[str]
    count: int
    summary: str


class PairedDevice(TypedDict):
    device_version: str | None
    device_type: str | None
    battery_status: str | None
    battery_level: int | None
    last_sync_time: str | None


class PairedDevices(TypedDict):
    devices: list[PairedDevice]
    count: int
