from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings
from pydantic import Field, ValidationInfo, field_validator


class Settings(BaseSettings):
    """Centralised application settings loaded from environment/.env."""

    bot_token: str = Field(..., env="BOT_TOKEN")
    jazz_sdk: str | None = Field(None, env="JAZZ_SDK")
    gigachat_auth: str | None = Field(None, env="GIGACHAT_AUTH")
    correct_secret_code: str | None = Field(None, env="CORRECT_SECRET_CODE")
    my_tg_id: int | None = Field(None, env="MY_TG_ID")

    rating_threshold: int = Field(1, env="RATING_THRESHOLD")
    refresh_check_period: int = Field(60, env="REFRESH_CHECK_PERIOD")
    invitation_timeout: int = Field(600, env="INVITATION_TIMEOUT")
    significant_difference: int = Field(1, env="SIGNIFICANT_DIFFERENCE")
    min_points_to_win: int = Field(1, env="MIN_POINTS_TO_WIN")
    case_read_time: int = Field(5 * 60, env="CASE_READ_TIME")
    link_follow_time: int = Field(2 * 60, env="LINK_FOLLOW_TIME")
    debate_time_minutes: int = Field(6, env="DEBATE_TIME_MINUTES")
    analyze_time_minutes: int = Field(14, env="ANALYZE_TIME_MINUTES")
    slot_duration_minutes: int | None = Field(
        None, env="SLOT_DURATION_MINUTES")
    default_room_count: int = Field(8, env="DEFAULT_ROOM_COUNT")

    attendance_grace_period: int = Field(
        2 * 60,
        description="How long to wait for late participants after slot start.",
    )
    attendance_poll_interval: int = Field(
        30, description="Polling interval in seconds.")

    case_dispatch_lead_seconds: int | None = None

    allowed_case_hours_msk: List[str] = Field(
        default_factory=lambda: ["17:30", "17:55"],
        description="List of hh:mm start times for tournament slots in MSK.",
    )

    class Config:
        env_file = Path(".env") 
        env_file_encoding = "utf-8"

    @field_validator("slot_duration_minutes", mode="before")
    def derive_slot_duration(cls, value: int | None, info: ValidationInfo) -> int | None:
        if value is not None:
            return value
        raw = info.data or {}
        debate_time = raw.get("debate_time_minutes")
        analyze_time = raw.get("analyze_time_minutes")
        if debate_time is None or analyze_time is None:
            return value
        return debate_time + analyze_time + 2

    @field_validator("case_dispatch_lead_seconds", mode="before")
    def derive_case_dispatch_lead(cls, value: int | None, info: ValidationInfo) -> int | None:
        if value is not None:
            return value
        raw = info.data or {}
        read_time = raw.get("case_read_time")
        link_time = raw.get("link_follow_time")
        if read_time is None or link_time is None:
            return value
        return read_time + link_time


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
