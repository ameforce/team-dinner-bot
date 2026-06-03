# -*- coding: utf-8 -*-
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from zoneinfo import ZoneInfo

from app.schedule.spec import ScheduleSpec

# Mon–Fri (no public-holiday calendar yet)
_BUSINESS_WEEKDAYS = frozenset(range(5))


def _to_local(anchor: datetime, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    if anchor.tzinfo is None:
        return anchor.replace(tzinfo=tz)
    return anchor.astimezone(tz)


def last_day_of_month(d: date) -> date:
    _, last = calendar.monthrange(d.year, d.month)
    return date(d.year, d.month, last)


def business_days_rest_of_month(*, after: datetime, tz_name: str) -> list[date]:
    """Today (local) through month-end, weekdays only (영업일 = 월~금)."""
    local = _to_local(after, tz_name)
    start = local.date()
    end = last_day_of_month(start)
    out: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() in _BUSINESS_WEEKDAYS:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def generate_poll_dates(
    spec: ScheduleSpec,
    tz_name: str,
    *,
    count: int = 5,
    after: datetime | None = None,
) -> list[date]:
    """Legacy helper: next N schedule-aligned dates (used in tests / reminders)."""
    anchor = after or datetime.now()
    anchor = _to_local(anchor, tz_name)

    dates: list[date] = []
    cursor = anchor
    for _ in range(count * 3):
        if len(dates) >= count:
            break
        nxt = spec.next_run_after(cursor, tz_name)
        d = nxt.date()
        if d not in dates:
            dates.append(d)
        cursor = nxt + timedelta(seconds=1)
    return dates[:count]


def format_date_ko(d: date) -> str:
    labels = ["\uc6d4", "\ud654", "\uc218", "\ubaa9", "\uae08", "\ud1a0", "\uc77c"]
    return f"{d.month}/{d.day}({labels[d.weekday()]})"
