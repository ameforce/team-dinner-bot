# -*- coding: utf-8 -*-
"""Canonical defaults for channel settings and poll timing."""

from __future__ import annotations

from app.runtime_defaults import DEFAULT_TIMEZONE as DEFAULT_TIMEZONE
from app.schedule.spec import ScheduleSpec, ScheduleType

DEFAULT_POLL_DURATION_HOURS = 24
MIN_POLL_DURATION_HOURS = 12
MAX_POLL_DURATION_HOURS = 168


def default_schedule_spec() -> ScheduleSpec:
    return ScheduleSpec(
        type=ScheduleType.WEEKLY_WEEKDAY,
        weekday=1,
        hour=10,
        minute=0,
    )


def clamp_poll_duration_hours(hours: int | None) -> int:
    if hours is None:
        return DEFAULT_POLL_DURATION_HOURS
    return max(MIN_POLL_DURATION_HOURS, min(MAX_POLL_DURATION_HOURS, hours))
