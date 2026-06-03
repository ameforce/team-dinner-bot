# -*- coding: utf-8 -*-
from __future__ import annotations

from app.integrations.google_calendar import GoogleCalendarClient, GoogleCalendarConfig


def test_google_calendar_client_refreshes_token_and_inserts_event():
    calls: list[tuple[str, dict, dict]] = []

    def request_json(url: str, data: dict, headers: dict) -> dict:
        calls.append((url, data, headers))
        if url == "https://oauth2.googleapis.com/token":
            return {"access_token": "ACCESS"}
        return {"htmlLink": "https://calendar.google.com/event?eid=1"}

    client = GoogleCalendarClient(
        GoogleCalendarConfig(
            client_id="CLIENT",
            client_secret="SECRET",
            refresh_token="REFRESH",
            calendar_id="team@example.com",
        ),
        request_json=request_json,
    )

    result = client.create_event({"summary": "Dinner"})

    assert result.ok is True
    assert result.html_link == "https://calendar.google.com/event?eid=1"
    assert calls[0] == (
        "https://oauth2.googleapis.com/token",
        {
            "client_id": "CLIENT",
            "client_secret": "SECRET",
            "refresh_token": "REFRESH",
            "grant_type": "refresh_token",
        },
        {},
    )
    assert calls[1] == (
        "https://www.googleapis.com/calendar/v3/calendars/team%40example.com/events?sendUpdates=all",
        {"summary": "Dinner"},
        {"Authorization": "Bearer ACCESS"},
    )


def test_google_calendar_client_reports_missing_refresh_token():
    client = GoogleCalendarClient(
        GoogleCalendarConfig(client_id="CLIENT", client_secret="SECRET", refresh_token="")
    )

    result = client.create_event({"summary": "Dinner"})

    assert result.ok is False
    assert "GOOGLE_REFRESH_TOKEN" in result.error
