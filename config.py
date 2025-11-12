import os
from dotenv import load_dotenv
from datetime import time, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from db.models import TimePreference

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SDK_KEY_ENCODED = os.getenv("JAZZ_SDK")
GIGACHAT_AUTH   = os.getenv("GIGACHAT_AUTH")
CORRECT_SECRET_CODE   = os.getenv("CORRECT_SECRET_CODE")

RATING_THRESHOLD = 1
REFRESH_CHECK_PERIOD = 60
INVITATION_TIMEOUT = 60 * 10

significant_difference = 1

GIGACHAT_MAX_RETRIES = 3
GIGACHAT_RETRY_DELAY = 1.0
GIGACHAT_MAX_RETRY_DELAY = 10.0

MIN_POINTS_TO_WIN = 1

CASE_READ_TIME = 5 * 60
LINK_FOLLOW_TIME = 2 * 60

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
UTC_TZ = timezone.utc

DEFAULT_ROOM_COUNT = 8

DEBATE_TIME_MINUTES = 6
analyze_time = 14
slot_duration_minutes = DEBATE_TIME_MINUTES + analyze_time + 10

_PERIOD_START_UTC = time(11, 0, tzinfo=UTC_TZ)
_PERIOD_END_UTC = time(13, 0, tzinfo=UTC_TZ)

PERIODS = {
    TimePreference.MORNING: {"start": _PERIOD_START_UTC, "end": _PERIOD_END_UTC},
    TimePreference.AFTERNOON: {"start": _PERIOD_START_UTC, "end": _PERIOD_END_UTC},
    TimePreference.EVENING: {"start": _PERIOD_START_UTC, "end": _PERIOD_END_UTC},
}

TOURNAMENT_SLOT_STARTS_MSK = [
    time(15, 30),
    time(16, 0),
    time(16, 30),
    time(17, 0),
]
