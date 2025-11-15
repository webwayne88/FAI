from datetime import time, timezone
from zoneinfo import ZoneInfo

from db.models import TimePreference
from app.config.settings import settings

BOT_TOKEN = settings.bot_token
SDK_KEY_ENCODED = settings.jazz_sdk
GIGACHAT_AUTH = settings.gigachat_auth
CORRECT_SECRET_CODE = settings.correct_secret_code

RATING_THRESHOLD = settings.rating_threshold
REFRESH_CHECK_PERIOD = settings.refresh_check_period
INVITATION_TIMEOUT = settings.invitation_timeout

significant_difference = settings.significant_difference

GIGACHAT_MAX_RETRIES = 3
GIGACHAT_RETRY_DELAY = 1.0
GIGACHAT_MAX_RETRY_DELAY = 10.0

MIN_POINTS_TO_WIN = settings.min_points_to_win

CASE_READ_TIME = settings.case_read_time
LINK_FOLLOW_TIME = settings.link_follow_time

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
UTC_TZ = timezone.utc

DEFAULT_ROOM_COUNT = settings.default_room_count

DEBATE_TIME_MINUTES = settings.debate_time_minutes
analyze_time = settings.analyze_time_minutes
slot_duration_minutes = settings.slot_duration_minutes

_PERIOD_START_UTC = time(11, 0, tzinfo=UTC_TZ)
_PERIOD_END_UTC = time(13, 0, tzinfo=UTC_TZ)

PERIODS = {
    TimePreference.MORNING: {"start": _PERIOD_START_UTC, "end": _PERIOD_END_UTC},
    TimePreference.AFTERNOON: {"start": _PERIOD_START_UTC, "end": _PERIOD_END_UTC},
    TimePreference.EVENING: {"start": _PERIOD_START_UTC, "end": _PERIOD_END_UTC},
}

TOURNAMENT_SLOT_STARTS_MSK = [
    time.fromisoformat(value) if isinstance(value, str) else value
    for value in settings.allowed_case_hours_msk
]
