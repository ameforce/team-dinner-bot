from datetime import datetime
from zoneinfo import ZoneInfo

from app.schedule.spec import ScheduleSpec, ScheduleType


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
