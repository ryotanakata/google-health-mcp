"""pydantic は Python 3.12 未満で typing.TypedDict を受け付けないため typing_extensions を使う。"""

from __future__ import annotations

from typing_extensions import TypedDict


class DailyActivity(TypedDict):
    date: str
    steps: int | None
    calories_out_kcal: int | None
    distance_km: float | None
    # Fitbit の「アクティブな時間」に相当する中強度以上（moderate + vigorous）の合計
    active_minutes: int
    active_minutes_by_level: dict[str, int]


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
    heart_rate_zone_minutes: dict[str, int]


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
