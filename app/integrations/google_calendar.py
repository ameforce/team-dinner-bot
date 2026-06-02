# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"


@dataclass(frozen=True)
class GoogleCalendarConfig:
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    calendar_id: str = "primary"


@dataclass(frozen=True)
class CalendarCreateResult:
    ok: bool
    html_link: str | None = None
    error: str | None = None


RequestJson = Callable[[str, dict, dict], dict]


class GoogleCalendarClient:
    def __init__(
        self,
        config: GoogleCalendarConfig,
        *,
        request_json: RequestJson | None = None,
    ) -> None:
        self.config = config
        self._request_json = request_json or _request_json

    def create_event(self, payload: dict) -> CalendarCreateResult:
        missing = self._missing_config()
        if missing:
            return CalendarCreateResult(ok=False, error=f"Missing {', '.join(missing)}")
        try:
            token = self._refresh_access_token()
            calendar_id = quote(self.config.calendar_id or "primary", safe="")
            url = f"{CALENDAR_EVENTS_URL.format(calendar_id=calendar_id)}?sendUpdates=all"
            result = self._request_json(url, payload, {"Authorization": f"Bearer {token}"})
            return CalendarCreateResult(ok=True, html_link=result.get("htmlLink"))
        except Exception as exc:
            return CalendarCreateResult(ok=False, error=str(exc))

    def _missing_config(self) -> list[str]:
        missing: list[str] = []
        if not self.config.client_id:
            missing.append("GOOGLE_CLIENT_ID")
        if not self.config.client_secret:
            missing.append("GOOGLE_CLIENT_SECRET")
        if not self.config.refresh_token:
            missing.append("GOOGLE_REFRESH_TOKEN")
        return missing

    def _refresh_access_token(self) -> str:
        result = self._request_json(
            TOKEN_URL,
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self.config.refresh_token,
                "grant_type": "refresh_token",
            },
            {},
        )
        token = result.get("access_token")
        if not token:
            raise ValueError("Google OAuth refresh response did not include access_token")
        return str(token)


def _request_json(url: str, data: dict, headers: dict) -> dict:
    if headers.get("Authorization"):
        encoded = json.dumps(data).encode("utf-8")
        content_type = "application/json"
    else:
        encoded = urlencode(data).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    request = Request(url, data=encoded, method="POST")
    for key, value in headers.items():
        request.add_header(key, value)
    request.add_header("Content-Type", content_type)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))
