"""Date and time helpers. Dates are stored in UTC; the mill works in its own time zone."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def now_utc():
    """Current time in UTC, cut to milliseconds (the precision MongoDB keeps)."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


def local_tz():
    return ZoneInfo(current_app.config["TIMEZONE"])


def to_local(value):
    if value is None:
        return None
    if value.tzinfo is None:                 # defensive: treat a naive value as UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_tz())


def now_local():
    return to_local(now_utc())


def today_local():
    return now_local().date()


def fmt_datetime(value):
    """07 OCT 2026 18:00 (the format printed on the queue label)."""
    local = to_local(value)
    if local is None:
        return ""
    return "%02d %s %d %02d:%02d" % (local.day, MONTHS[local.month - 1], local.year, local.hour, local.minute)


def fmt_date(value):
    local = to_local(value)
    return "" if local is None else "%02d %s %d" % (local.day, MONTHS[local.month - 1], local.year)


def fmt_time(value):
    local = to_local(value)
    return "" if local is None else "%02d:%02d" % (local.hour, local.minute)


def parse_local_datetime(text):
    """'2026-10-09T14:30' from a datetime-local field, in the mill's time zone -> UTC. None when blank or invalid."""
    try:
        value = datetime.strptime((text or "").strip()[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return value.replace(tzinfo=local_tz()).astimezone(timezone.utc)


def input_local_datetime(value):
    """A stored UTC time as the value of a datetime-local field."""
    local = to_local(value)
    return "" if local is None else local.strftime("%Y-%m-%dT%H:%M")
