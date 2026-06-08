from datetime import datetime
from zoneinfo import ZoneInfo

from app.schedule.spec import ScheduleSpec, ScheduleType
from app.settings_defaults import default_schedule_spec


def test_default_schedule_spec_uses_monthly_day_after_weekly_ui_retirement():
    spec = default_schedule_spec()

    assert spec.type == ScheduleType.MONTHLY_DAY_OF_MONTH
    assert spec.day == 15
    assert spec.month_interval == 1
    assert spec.hour == 10


def test_weekly_next_tuesday():
    spec = ScheduleSpec(type=ScheduleType.WEEKLY_WEEKDAY, weekday=1, hour=10, minute=0)
    after = datetime(2026, 5, 18, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # Monday
    nxt = spec.next_run_after(after, "Asia/Seoul")
    assert nxt.weekday() == 1
    assert nxt > after


def test_monthly_day_15():
    spec = ScheduleSpec(type=ScheduleType.MONTHLY_DAY_OF_MONTH, day=15, hour=10, minute=0)
    after = datetime(2026, 5, 20, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    nxt = spec.next_run_after(after, "Asia/Seoul")
    assert nxt.day == 15
    assert nxt.month == 6


def test_monthly_day_uses_month_interval_after_current_month_passed():
    spec = ScheduleSpec(
        type=ScheduleType.MONTHLY_DAY_OF_MONTH,
        day=15,
        month_interval=2,
        hour=10,
        minute=0,
    )
    after = datetime(2026, 5, 20, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    nxt = spec.next_run_after(after, "Asia/Seoul")

    assert nxt.year == 2026
    assert nxt.month == 7
    assert nxt.day == 15
    assert spec.describe_ko() == "2개월마다 15일 10:00"


def test_monthly_day_interval_uses_saved_anchor_after_restart():
    spec = ScheduleSpec(
        type=ScheduleType.MONTHLY_DAY_OF_MONTH,
        day=15,
        month_interval=2,
        month_anchor_year=2026,
        month_anchor_month=5,
        hour=10,
        minute=0,
    )
    after = datetime(2026, 6, 1, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    nxt = spec.next_run_after(after, "Asia/Seoul")

    assert nxt.year == 2026
    assert nxt.month == 7
    assert nxt.day == 15


def test_monthly_second_tuesday():
    spec = ScheduleSpec(
        type=ScheduleType.MONTHLY_NTH_WEEKDAY,
        weekday=1,
        nth=2,
        hour=10,
        minute=0,
    )
    after = datetime(2026, 5, 1, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    nxt = spec.next_run_after(after, "Asia/Seoul")
    assert nxt.weekday() == 1
    assert 8 <= nxt.day <= 14


def test_monthly_nth_weekday_uses_month_interval_after_current_month_passed():
    spec = ScheduleSpec(
        type=ScheduleType.MONTHLY_NTH_WEEKDAY,
        weekday=1,
        nth=2,
        month_interval=3,
        hour=10,
        minute=0,
    )
    after = datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    nxt = spec.next_run_after(after, "Asia/Seoul")

    assert nxt.year == 2026
    assert nxt.month == 8
    assert nxt.weekday() == 1
    assert 8 <= nxt.day <= 14
    assert spec.describe_ko() == "3개월마다 2번째 화요일 10:00"


def test_monthly_nth_weekday_interval_uses_saved_anchor_after_restart():
    spec = ScheduleSpec(
        type=ScheduleType.MONTHLY_NTH_WEEKDAY,
        weekday=1,
        nth=2,
        month_interval=3,
        month_anchor_year=2026,
        month_anchor_month=5,
        hour=10,
        minute=0,
    )
    after = datetime(2026, 6, 1, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    nxt = spec.next_run_after(after, "Asia/Seoul")

    assert nxt.year == 2026
    assert nxt.month == 8
    assert nxt.weekday() == 1
    assert 8 <= nxt.day <= 14


def test_legacy_monthly_schedule_defaults_to_one_month_interval():
    spec = ScheduleSpec.model_validate(
        {"type": "MONTHLY_DAY_OF_MONTH", "day": 15, "hour": 10, "minute": 0}
    )

    assert spec.month_interval == 1
