"""Date and timezone helpers.

Timestamps are stored in UTC and displayed in the household timezone. Date-only
values are calendar dates and never shift when timezone settings change.
"""

import calendar
import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.utils import timezone

UNITS = ("day", "week", "month", "year")


def add_interval(anchor: dt.date, count: int, unit: str) -> dt.date:
    """Return ``anchor`` advanced by ``count`` units.

    Months and years clamp to the end of shorter months, computed from the
    anchor each time so a 31st anchor returns to the 31st where it exists and a
    29 February anchor lands on 28 February in non-leap years.
    """
    if unit == "day":
        return anchor + dt.timedelta(days=count)
    if unit == "week":
        return anchor + dt.timedelta(weeks=count)
    if unit == "month":
        months = anchor.month - 1 + count
        year = anchor.year + months // 12
        month = months % 12 + 1
    elif unit == "year":
        year = anchor.year + count
        month = anchor.month
    else:
        raise ValueError(f"Unknown interval unit: {unit}")
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def timezone_choices():
    return sorted(available_timezones())


def local_to_utc(day: dt.date, at: dt.time, zone_name: str) -> dt.datetime:
    """Convert a household-local wall time to UTC.

    Ambiguous times (clocks going back) resolve to the first occurrence so they
    fire once. Nonexistent times (clocks going forward) move to the next valid
    local time.
    """
    zone = get_zone(zone_name)
    naive = dt.datetime.combine(day, at)
    candidate = naive
    for _ in range(24 * 60):
        aware = candidate.replace(tzinfo=zone, fold=0)
        back = aware.astimezone(dt.UTC).astimezone(zone).replace(tzinfo=None)
        if back == candidate:
            return aware.astimezone(dt.UTC)
        candidate = (candidate + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    raise ValueError("Could not resolve local time")  # pragma: no cover


def household_today(household) -> dt.date:
    return timezone.now().astimezone(get_zone(household.timezone)).date()


def to_household_time(value: dt.datetime, household) -> dt.datetime:
    return value.astimezone(get_zone(household.timezone))
