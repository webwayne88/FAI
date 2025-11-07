import os
from dotenv import load_dotenv
from datetime import time, datetime, timedelta

from db.models import TimePreference

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SDK_KEY_ENCODED = os.getenv("JAZZ_SDK")
GIGACHAT_AUTH   = os.getenv("GIGACHAT_AUTH")
CORRECT_SECRET_CODE   = os.getenv("CORRECT_SECRET_CODE")

RATING_THRESHOLD = 1
REFRESH_CHECK_PERIOD = 60
INVITATION_TIMEOUT = 60 * 1

significant_difference = 1

GIGACHAT_MAX_RETRIES = 3
GIGACHAT_RETRY_DELAY = 1.0
GIGACHAT_MAX_RETRY_DELAY = 10.0

MIN_POINTS_TO_WIN = 1

CASE_READ_TIME = 300
LINK_FOLLOW_TIME = 120


'''

analyze_time = 15 
slot_duration_minutes = 15 + analyze_time
PERIODS = {
    TimePreference.EVENING: {
        "start": time(18, 0),
        "end": time(23, 0),
    }
}
'''
#для тестирования       
analyze_time = 8 
slot_duration_minutes =  2 + analyze_time
PERIODS = {
    TimePreference.EVENING: {
        "start": (datetime.now() + timedelta(minutes=1)).time(),
        "end": (datetime.now() + timedelta(minutes=11)).time(),
    }
}

