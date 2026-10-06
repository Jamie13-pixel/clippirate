import time
from datetime import date

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# --- Global daily cap, independent of per-IP limits ---
# Protects your Groq/Pexels/TTS usage from runaway costs regardless of who's calling.
DAILY_GENERATION_LIMIT = 50  # adjust to whatever your budget/quotas comfortably support

_daily_counter = {"date": None, "count": 0}


def check_daily_limit():
    today = date.today().isoformat()
    if _daily_counter["date"] != today:
        _daily_counter["date"] = today
        _daily_counter["count"] = 0

    if _daily_counter["count"] >= DAILY_GENERATION_LIMIT:
        return False

    _daily_counter["count"] += 1
    return True


def get_daily_usage():
    today = date.today().isoformat()
    if _daily_counter["date"] != today:
        return 0
    return _daily_counter["count"]