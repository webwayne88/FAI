from __future__ import annotations

from datetime import datetime

from config import MOSCOW_TZ, UTC_TZ


def ensure_utc(dt: datetime) -> datetime:
    """Return a timezone-aware datetime in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(UTC_TZ)


def as_utc_naive(dt: datetime) -> datetime:
    """Convert datetime to UTC and drop tzinfo for storage in naive columns."""
    return ensure_utc(dt).replace(tzinfo=None)


def to_moscow(dt: datetime) -> datetime:
    """Convert a datetime (naive or tz-aware) to Moscow timezone."""
    return ensure_utc(dt).astimezone(MOSCOW_TZ)


def format_moscow(dt: datetime, fmt: str) -> str:
    """Format datetime in Moscow timezone using the provided format string."""
    return to_moscow(dt).strftime(fmt)