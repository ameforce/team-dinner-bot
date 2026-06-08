# -*- coding: utf-8 -*-
"""Human-friendly schedule specs (no cron in UI)."""

from __future__ import annotations

from app import messages as m

from datetime import date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from dateutil.relativedelta import FR, MO, SA, SU, TH, TU, WE, relativedelta
from pydantic import BaseModel, Field, field_validator

WEEKDAY_MAP = {
    0: MO,
    1: TU,
    2: WE,
    3: TH,
    4: FR,
    5: SA,
    6: SU,
}

def weekday_label_ko(weekday: int) -> str:
    if 0 <= weekday <= 4:
        return m.WEEKDAYS[weekday]
    all_labels = getattr(m, "WEEKDAYS_ALL", None) or m.WEEKDAYS
    return all_labels[weekday] if 0 <= weekday <= 6 else "?"


class ScheduleType(str, Enum):
    WEEKLY_WEEKDAY = "WEEKLY_WEEKDAY"
    MONTHLY_DAY_OF_MONTH = "MONTHLY_DAY_OF_MONTH"
    MONTHLY_NTH_WEEKDAY = "MONTHLY_NTH_WEEKDAY"


class ScheduleSpec(BaseModel):
    type: ScheduleType
    weekday: int | None = Field(default=None, ge=0, le=4)
    day: int | None = Field(default=None, ge=1, le=28)
    nth: int | None = Field(default=None, ge=-1, le=4)
    month_interval: int = Field(default=1, ge=1, le=12)
    month_anchor_year: int | None = Field(default=None, ge=1970, le=9999)
    month_anchor_month: int | None = Field(default=None, ge=1, le=12)
    hour: int = Field(default=10, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)

    @field_validator("day")
    @classmethod
    def clamp_day(cls, v: int | None) -> int | None:
        if v is not None and v > 28:
            return 28
        return v

    def describe_ko(self) -> str:
        if self.type == ScheduleType.WEEKLY_WEEKDAY and self.weekday is not None:
            return f"매주 {weekday_label_ko(self.weekday)}요일 {self.hour:02d}:{self.minute:02d}"
        month_prefix = "매월" if self.month_interval == 1 else f"{self.month_interval}개월마다"
        if self.type == ScheduleType.MONTHLY_DAY_OF_MONTH and self.day is not None:
            return f"{month_prefix} {self.day}일 {self.hour:02d}:{self.minute:02d}"
        if (
            self.type == ScheduleType.MONTHLY_NTH_WEEKDAY
            and self.weekday is not None
            and self.nth is not None
        ):
            nth_label = "마지막" if self.nth == -1 else f"{self.nth}번째"
            return f"{month_prefix} {nth_label} {weekday_label_ko(self.weekday)}요일 {self.hour:02d}:{self.minute:02d}"
        return "미설정"

    def next_run_after(self, after: datetime, tz_name: str) -> datetime:
        tz = ZoneInfo(tz_name)
        if after.tzinfo is None:
            after = after.replace(tzinfo=tz)
        else:
            after = after.astimezone(tz)

        if self.type == ScheduleType.WEEKLY_WEEKDAY:
            return self._next_weekly(after, tz)
        if self.type == ScheduleType.MONTHLY_DAY_OF_MONTH:
            return self._next_monthly_day(after, tz)
        if self.type == ScheduleType.MONTHLY_NTH_WEEKDAY:
            return self._next_monthly_nth_weekday(after, tz)
        raise ValueError(f"Unsupported schedule type: {self.type}")

    def with_month_anchor(self, anchor: datetime, tz_name: str) -> ScheduleSpec:
        tz = ZoneInfo(tz_name)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=tz)
        else:
            anchor = anchor.astimezone(tz)
        if self.type not in {
            ScheduleType.MONTHLY_DAY_OF_MONTH,
            ScheduleType.MONTHLY_NTH_WEEKDAY,
        } or self.month_interval == 1:
            return self.model_copy(
                update={"month_anchor_year": None, "month_anchor_month": None}
            )
        return self.model_copy(
            update={
                "month_anchor_year": anchor.year,
                "month_anchor_month": anchor.month,
            }
        )

    def _at_time(self, d: date, tz: ZoneInfo) -> datetime:
        return datetime(d.year, d.month, d.day, self.hour, self.minute, tzinfo=tz)

    def _next_weekly(self, after: datetime, tz: ZoneInfo) -> datetime:
        assert self.weekday is not None
        target_weekday = self.weekday
        days_ahead = (target_weekday - after.weekday()) % 7
        candidate_date = after.date() + timedelta(days=days_ahead)
        candidate = self._at_time(candidate_date, tz)
        if candidate <= after:
            candidate = self._at_time(candidate_date + timedelta(days=7), tz)
        return candidate

    def _next_monthly_day(self, after: datetime, tz: ZoneInfo) -> datetime:
        assert self.day is not None
        year, month = self._first_monthly_candidate_month(after)
        for _ in range(24):
            day = min(self.day, self._last_day_of_month(year, month))
            candidate = self._at_time(date(year, month, day), tz)
            if candidate > after:
                return candidate
            year, month = self._add_months(year, month, self.month_interval)
        raise RuntimeError("Could not compute next monthly day within 24 months")

    def _next_monthly_nth_weekday(self, after: datetime, tz: ZoneInfo) -> datetime:
        assert self.weekday is not None and self.nth is not None
        year, month = self._first_monthly_candidate_month(after)
        for _ in range(24):
            candidate_date = self._nth_weekday_in_month(year, month, self.weekday, self.nth)
            if candidate_date:
                candidate = self._at_time(candidate_date, tz)
                if candidate > after:
                    return candidate
            year, month = self._add_months(year, month, self.month_interval)
        raise RuntimeError("Could not compute next nth weekday within 24 months")

    @staticmethod
    def _last_day_of_month(year: int, month: int) -> int:
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        return (next_month - timedelta(days=1)).day

    @staticmethod
    def _add_months(year: int, month: int, months: int) -> tuple[int, int]:
        next_month = date(year, month, 1) + relativedelta(months=months)
        return next_month.year, next_month.month

    def _first_monthly_candidate_month(self, after: datetime) -> tuple[int, int]:
        if (
            self.month_interval == 1
            or self.month_anchor_year is None
            or self.month_anchor_month is None
        ):
            return after.year, after.month

        anchor_index = self.month_anchor_year * 12 + self.month_anchor_month - 1
        after_index = after.year * 12 + after.month - 1
        if after_index <= anchor_index:
            candidate_index = anchor_index
        else:
            months_since_anchor = after_index - anchor_index
            remainder = months_since_anchor % self.month_interval
            candidate_index = after_index
            if remainder:
                candidate_index += self.month_interval - remainder
        year, month_zero_based = divmod(candidate_index, 12)
        return year, month_zero_based + 1

    @staticmethod
    def _nth_weekday_in_month(year: int, month: int, weekday: int, nth: int) -> date | None:
        wd = WEEKDAY_MAP[weekday]
        if nth == -1:
            if month == 12:
                first_next = date(year + 1, 1, 1)
            else:
                first_next = date(year, month + 1, 1)
            last_of_month = first_next - timedelta(days=1)
            dt = datetime(last_of_month.year, last_of_month.month, last_of_month.day)
            while dt.weekday() != weekday:
                dt -= timedelta(days=1)
            return dt.date()

        first = date(year, month, 1)
        dt = datetime(first.year, first.month, first.day) + relativedelta(weekday=wd(+nth))
        if dt.month != month:
            return None
        return dt.date()
