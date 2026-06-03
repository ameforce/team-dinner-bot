# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

CALENDAR_EVENTEDIT_URL = "https://calendar.google.com/calendar/r/eventedit"
DEFAULT_START_HOUR = 18
DEFAULT_START_MINUTE = 30
DEFAULT_DURATION_HOURS = 2
BOOKING_URL_MISSING = "예약 링크 미설정"


@dataclass(frozen=True)
class DinnerCalendarEvent:
    title: str
    dinner_date: date
    tz_name: str
    booking_url: str | None = None
    attendee_emails: list[str] = field(default_factory=list)
    optional_attendee_emails: list[str] = field(default_factory=list)
    missing_member_ids: list[str] = field(default_factory=list)
    start_hour: int = DEFAULT_START_HOUR
    start_minute: int = DEFAULT_START_MINUTE
    duration_hours: int = DEFAULT_DURATION_HOURS


def _format_calendar_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_google_calendar_url(event: DinnerCalendarEvent) -> str:
    start, end = _event_datetimes(event)
    emails = [email for email in event.attendee_emails if email]
    params = {
        "action": "TEMPLATE",
        "text": event.title,
        "dates": f"{_format_calendar_datetime(start)}/{_format_calendar_datetime(end)}",
        "stz": event.tz_name,
        "etz": event.tz_name,
        "ctz": event.tz_name,
        "details": build_calendar_details(event),
        "location": event.booking_url or BOOKING_URL_MISSING,
    }
    if emails:
        params["add"] = ",".join(emails)
    return f"{CALENDAR_EVENTEDIT_URL}?{urlencode(params)}"


def build_google_calendar_event_payload(event: DinnerCalendarEvent) -> dict:
    start, end = _event_datetimes(event)
    attendees = [
        {"email": email, "optional": False}
        for email in event.attendee_emails
        if email
    ]
    attendees.extend(
        {"email": email, "optional": True}
        for email in event.optional_attendee_emails
        if email
    )
    payload = {
        "summary": event.title,
        "location": event.booking_url or BOOKING_URL_MISSING,
        "description": build_calendar_details(event),
        "start": {"dateTime": start.isoformat(), "timeZone": event.tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": event.tz_name},
    }
    if attendees:
        payload["attendees"] = attendees
    return payload


def _event_datetimes(event: DinnerCalendarEvent) -> tuple[datetime, datetime]:
    tz = ZoneInfo(event.tz_name)
    start = datetime(
        event.dinner_date.year,
        event.dinner_date.month,
        event.dinner_date.day,
        event.start_hour,
        event.start_minute,
        tzinfo=tz,
    )
    return start, start + timedelta(hours=event.duration_hours)


def build_calendar_details(event: DinnerCalendarEvent) -> str:
    booking_url = event.booking_url or BOOKING_URL_MISSING
    lines = [
        "Slack 회식 투표에서 확정된 일정입니다.",
        "",
        f"예약 링크: {booking_url}",
        "",
    ]
    if event.attendee_emails:
        lines.append("참석 후보 이메일:")
        lines.extend(f"- {email}" for email in event.attendee_emails if email)
    else:
        lines.append("참석 후보 이메일: 확인된 이메일 없음")
    if event.missing_member_ids:
        lines.extend(["", "Slack 이메일을 확인하지 못한 멤버:"])
        lines.extend(f"- {user_id}" for user_id in event.missing_member_ids)
    lines.extend(
        [
            "",
            "이 링크는 예약 담당자의 브라우저 Google 계정으로 새 일정을 여는 링크입니다.",
        ]
    )
    return "\n".join(lines)
