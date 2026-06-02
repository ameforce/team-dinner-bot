# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

from app.integrations.calendar_links import (
    DinnerCalendarEvent,
    build_google_calendar_event_payload,
    build_google_calendar_url,
)


def test_calendar_link_prefills_required_google_fields():
    url = build_google_calendar_url(
        DinnerCalendarEvent(
            title="Team Dinner Bot 회식",
            dinner_date=date(2099, 6, 20),
            tz_name="Asia/Seoul",
            booking_url="https://example.com/book?room=4",
            attendee_emails=["alpha@example.com", "beta@example.com"],
            missing_member_ids=["U_MISSING"],
        )
    )

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "calendar.google.com"
    assert parsed.path == "/calendar/r/eventedit"
    assert qs["action"] == ["TEMPLATE"]
    assert qs["text"] == ["Team Dinner Bot 회식"]
    assert qs["dates"] == ["20990620T093000Z/20990620T113000Z"]
    assert qs["stz"] == ["Asia/Seoul"]
    assert qs["etz"] == ["Asia/Seoul"]
    assert qs["ctz"] == ["Asia/Seoul"]
    assert qs["location"] == ["https://example.com/book?room=4"]
    assert qs["add"] == ["alpha@example.com,beta@example.com"]
    details = qs["details"][0]
    assert "https://example.com/book?room=4" in details
    assert "alpha@example.com" in details
    assert "beta@example.com" in details
    assert "U_MISSING" in details


def test_calendar_link_handles_missing_attendee_emails_without_failing():
    url = build_google_calendar_url(
        DinnerCalendarEvent(
            title="Team Dinner Bot 회식",
            dinner_date=date(2099, 6, 20),
            tz_name="Asia/Seoul",
            booking_url=None,
            attendee_emails=[],
            missing_member_ids=["U1"],
        )
    )

    qs = parse_qs(urlparse(url).query)
    assert "add" not in qs
    assert "예약 링크 미설정" in qs["details"][0]
    assert "U1" in qs["details"][0]


def test_calendar_event_payload_marks_optional_attendees():
    payload = build_google_calendar_event_payload(
        DinnerCalendarEvent(
            title="Team Dinner Bot 회식",
            dinner_date=date(2099, 6, 20),
            tz_name="Asia/Seoul",
            booking_url="https://booking.example.com",
            attendee_emails=["required@example.com"],
            optional_attendee_emails=["optional@example.com"],
        )
    )

    assert payload["summary"] == "Team Dinner Bot 회식"
    assert payload["attendees"] == [
        {"email": "required@example.com", "optional": False},
        {"email": "optional@example.com", "optional": True},
    ]
    assert payload["start"]["timeZone"] == "Asia/Seoul"
