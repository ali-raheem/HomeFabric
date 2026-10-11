"""Pure recurrence calculations (no database access)."""

import datetime as dt

from homefabric.core.dates import add_interval


def fixed_occurrence(anchor: dt.date, index: int, count: int, unit: str) -> dt.date:
    return add_interval(anchor, index * count, unit)


def fixed_occurrences(anchor: dt.date, count: int, unit: str, *, start_index: int = 0,
                      until: dt.date | None = None, limit: int = 1000):
    """Yield (index, date) pairs from ``start_index`` while date <= until."""
    index = start_index
    produced = 0
    while produced < limit:
        day = fixed_occurrence(anchor, index, count, unit)
        if until is not None and day > until:
            return
        yield index, day
        index += 1
        produced += 1


def first_index_on_or_after(anchor: dt.date, count: int, unit: str, day: dt.date) -> int:
    """Smallest index whose occurrence is on or after ``day``."""
    if day <= anchor:
        return 0
    # Estimate then correct; avoids iterating years of daily occurrences.
    span_days = (day - anchor).days
    approx = {"day": 1, "week": 7, "month": 28, "year": 365}[unit] * count
    index = max(0, span_days // approx - 2)
    while fixed_occurrence(anchor, index, count, unit) >= day and index > 0:
        index -= 1
    while fixed_occurrence(anchor, index, count, unit) < day:
        index += 1
    return index


def next_after_completion(completed_on: dt.date, count: int, unit: str) -> dt.date:
    return add_interval(completed_on, count, unit)
