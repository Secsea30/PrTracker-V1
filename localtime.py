"""
Everything is stored internally in UTC (unambiguous, safe for logs/history),
but displayed in the team's local time zone — Gulf Standard Time (UTC+4,
Abu Dhabi/Dubai), since that's where the team actually is.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Dubai")


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(LOCAL_TZ)


def format_local(dt: datetime, with_seconds: bool = False) -> str:
    pattern = "%-d %B %Y, %-I:%M:%S %p GST" if with_seconds else "%-d %B %Y, %-I:%M %p GST"
    return to_local(dt).strftime(pattern)
