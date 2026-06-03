# -*- coding: utf-8 -*-
"""Slack modals for channel settings."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app import messages as m
from app.schedule.spec import ScheduleSpec, ScheduleType
from app.settings_defaults import (
    DEFAULT_POLL_DURATION_HOURS,
    MIN_POLL_DURATION_HOURS,
    clamp_poll_duration_hours,
    default_schedule_spec,
)
from app.workflow.participants import CalendarInvitee

SCHEDULE_TYPE_OPTIONS = [
    {"text": {"type": "plain_text", "text": m.OPT_WEEKLY}, "value": "WEEKLY_WEEKDAY"},
    {"text": {"type": "plain_text", "text": m.OPT_MONTHLY_DAY}, "value": "MONTHLY_DAY_OF_MONTH"},
    {"text": {"type": "plain_text", "text": m.OPT_MONTHLY_NTH}, "value": "MONTHLY_NTH_WEEKDAY"},
]

MAX_BOOKING_URL_LENGTH = 2048
MAX_EMAIL_LENGTH = 254
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")

WEEKDAY_OPTIONS = [
    {"text": {"type": "plain_text", "text": label}, "value": str(i)}
    for i, label in enumerate(m.WEEKDAYS)
]


def _option(value: str, label: str) -> dict:
    return {"text": {"type": "plain_text", "text": label}, "value": value}


def _initial_select(options: list[dict], value: str | None) -> dict | None:
    if value is None:
        return None
    for opt in options:
        if opt["value"] == value:
            return opt
    return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value and value not in seen:
            unique_values.append(value)
            seen.add(value)
    return unique_values


def _multi_users_element(initial_user_ids: list[str], placeholder: str) -> dict:
    element: dict = {
        "type": "multi_users_select",
        "action_id": "value",
        "placeholder": {"type": "plain_text", "text": placeholder},
    }
    initial_users = _unique(initial_user_ids)
    if initial_users:
        element["initial_users"] = initial_users
    return element


def _invitee_values(invitees: list[CalendarInvitee], role: str, kind: str) -> list[str]:
    return [invitee.value for invitee in invitees if invitee.role == role and invitee.kind == kind]


def settings_modal(
    channel_id: str,
    *,
    spec: ScheduleSpec | None = None,
    poll_duration_hours: int | None = None,
    booking_url: str | None = None,
    poll_target_ids: list[str] | None = None,
    calendar_invitees: list[CalendarInvitee] | None = None,
) -> dict:
    spec = spec or default_schedule_spec()
    poll_hours = clamp_poll_duration_hours(poll_duration_hours)
    booking_url = booking_url or ""
    poll_target_ids = poll_target_ids or []
    calendar_invitees = calendar_invitees or []

    type_initial = _initial_select(SCHEDULE_TYPE_OPTIONS, spec.type.value)
    weekday_initial = _initial_select(WEEKDAY_OPTIONS, str(spec.weekday if spec.weekday is not None else 1))

    schedule_elem: dict = {
        "type": "static_select",
        "action_id": "value",
        "options": SCHEDULE_TYPE_OPTIONS,
    }
    if type_initial:
        schedule_elem["initial_option"] = type_initial

    weekday_elem: dict = {
        "type": "static_select",
        "action_id": "value",
        "options": WEEKDAY_OPTIONS,
    }
    if weekday_initial:
        weekday_elem["initial_option"] = weekday_initial

    return {
        "type": "modal",
        "callback_id": "settings_submit",
        "private_metadata": channel_id,
        "title": {"type": "plain_text", "text": m.MODAL_TITLE},
        "submit": {"type": "plain_text", "text": m.MODAL_SAVE},
        "close": {"type": "plain_text", "text": m.MODAL_CANCEL},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": m.MODAL_HELP},
            },
            {
                "type": "input",
                "block_id": "schedule_type",
                "label": {"type": "plain_text", "text": m.LABEL_SCHEDULE_TYPE},
                "element": schedule_elem,
            },
            {
                "type": "input",
                "block_id": "weekday",
                "optional": True,
                "label": {"type": "plain_text", "text": m.LABEL_WEEKDAY},
                "element": weekday_elem,
            },
            {
                "type": "input",
                "block_id": "day_of_month",
                "optional": True,
                "label": {"type": "plain_text", "text": m.LABEL_DAY},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(spec.day if spec.day is not None else 15),
                    "placeholder": {"type": "plain_text", "text": "15"},
                },
            },
            {
                "type": "input",
                "block_id": "nth",
                "optional": True,
                "label": {"type": "plain_text", "text": m.LABEL_NTH},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(spec.nth if spec.nth is not None else 2),
                    "placeholder": {"type": "plain_text", "text": "2 (둘째 주), -1=마지막"},
                },
            },
            {
                "type": "input",
                "block_id": "poll_hour",
                "label": {"type": "plain_text", "text": m.LABEL_HOUR},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(spec.hour),
                },
            },
            {
                "type": "input",
                "block_id": "poll_duration",
                "label": {"type": "plain_text", "text": m.LABEL_POLL_HOURS},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(poll_hours),
                    "placeholder": {
                        "type": "plain_text",
                        "text": f"{DEFAULT_POLL_DURATION_HOURS} (최소 {MIN_POLL_DURATION_HOURS})",
                    },
                },
            },
            {
                "type": "input",
                "block_id": "booking_url",
                "optional": True,
                "label": {"type": "plain_text", "text": m.LABEL_BOOKING_URL},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": booking_url,
                    "placeholder": {"type": "plain_text", "text": "https://..."},
                },
            },
            {
                "type": "input",
                "block_id": "poll_targets",
                "optional": True,
                "label": {"type": "plain_text", "text": "투표 대상"},
                "element": _multi_users_element(poll_target_ids, "투표 대상 선택"),
            },
            {
                "type": "input",
                "block_id": "calendar_required",
                "optional": True,
                "label": {"type": "plain_text", "text": "캘린더 필수 초대"},
                "element": _multi_users_element(
                    _invitee_values(calendar_invitees, "required", "slack"),
                    "필수 초대자 선택",
                ),
            },
            {
                "type": "input",
                "block_id": "calendar_required_emails",
                "optional": True,
                "label": {"type": "plain_text", "text": "캘린더 필수 외부 이메일"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": _join_values(
                        _invitee_values(calendar_invitees, "required", "email")
                    ),
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "calendar_optional",
                "optional": True,
                "label": {"type": "plain_text", "text": "캘린더 선택 초대"},
                "element": _multi_users_element(
                    _invitee_values(calendar_invitees, "optional", "slack"),
                    "선택 초대자 선택",
                ),
            },
            {
                "type": "input",
                "block_id": "calendar_optional_emails",
                "optional": True,
                "label": {"type": "plain_text", "text": "캘린더 선택 외부 이메일"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": _join_values(
                        _invitee_values(calendar_invitees, "optional", "email")
                    ),
                    "multiline": True,
                },
            },
            {
                "type": "input",
                "block_id": "calendar_excluded",
                "optional": True,
                "label": {"type": "plain_text", "text": "캘린더 초대 제외"},
                "element": _multi_users_element(
                    _invitee_values(calendar_invitees, "excluded", "slack"),
                    "초대 제외자 선택",
                ),
            },
            {
                "type": "input",
                "block_id": "calendar_excluded_emails",
                "optional": True,
                "label": {"type": "plain_text", "text": "캘린더 제외 외부 이메일"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": _join_values(
                        _invitee_values(calendar_invitees, "excluded", "email")
                    ),
                    "multiline": True,
                },
            },
        ],
    }


def loading_settings_modal(channel_id: str) -> dict:
    return {
        "type": "modal",
        "callback_id": "settings_loading",
        "private_metadata": channel_id,
        "title": {"type": "plain_text", "text": m.MODAL_TITLE},
        "close": {"type": "plain_text", "text": m.MODAL_CANCEL},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "설정을 불러오는 중입니다. 잠시만 기다려 주세요."},
            }
        ],
    }


def _join_values(values: list[str]) -> str:
    return ", ".join(values)


def parse_settings_submission(view: dict) -> tuple[ScheduleSpec, int, str | None]:
    values = _view_values(view)

    def pick(block_id: str, field: str = "value") -> str | None:
        block = _block_values(values, block_id)
        elem = _element_values(block, field)
        if not elem:
            return None
        selected = elem.get("selected_option")
        if selected:
            if not isinstance(selected, dict):
                raise ValueError(f"{block_id} selected_option invalid")
            value = selected.get("value")
            if not isinstance(value, str) or not value:
                raise ValueError(f"{block_id} selected_option value invalid")
            return value
        value = elem.get("value")
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{block_id} value invalid")
        return value

    schedule_type = pick("schedule_type")
    if not schedule_type:
        raise ValueError("schedule_type required")

    weekday_raw = pick("weekday")
    day_raw = pick("day_of_month")
    nth_raw = pick("nth")
    hour_raw = pick("poll_hour") or "10"
    duration_raw = pick("poll_duration") or str(DEFAULT_POLL_DURATION_HOURS)
    booking_url = _validate_booking_url((pick("booking_url") or "").strip() or None)

    hour = int(hour_raw)
    poll_duration = clamp_poll_duration_hours(int(duration_raw))

    spec_kwargs: dict = {
        "type": ScheduleType(schedule_type),
        "hour": hour,
        "minute": 0,
    }
    if weekday_raw is not None and weekday_raw != "":
        spec_kwargs["weekday"] = int(weekday_raw)
    if day_raw:
        spec_kwargs["day"] = int(day_raw)
    if nth_raw:
        spec_kwargs["nth"] = int(nth_raw)

    if spec_kwargs["type"] == ScheduleType.WEEKLY_WEEKDAY:
        spec_kwargs.setdefault("weekday", 1)
    elif spec_kwargs["type"] == ScheduleType.MONTHLY_DAY_OF_MONTH:
        spec_kwargs.setdefault("day", 15)
    elif spec_kwargs["type"] == ScheduleType.MONTHLY_NTH_WEEKDAY:
        spec_kwargs.setdefault("weekday", 1)
        spec_kwargs.setdefault("nth", 2)

    return ScheduleSpec(**spec_kwargs), poll_duration, booking_url


def parse_participant_settings_submission(view: dict) -> tuple[list[str], list[CalendarInvitee]]:
    values = _view_values(view)

    def pick_element(block_id: str) -> dict:
        block = _block_values(values, block_id)
        return _element_values(block, "value") or {}

    def pick_text(block_id: str) -> str:
        value = pick_element(block_id).get("value") or ""
        if not isinstance(value, str):
            raise ValueError(f"{block_id} text invalid")
        return value.strip()

    def pick_users(block_id: str) -> list[str]:
        elem = pick_element(block_id)
        if "selected_users" in elem:
            selected_users = elem["selected_users"]
            if not isinstance(selected_users, list) or any(
                not isinstance(user_id, str) for user_id in selected_users
            ):
                raise ValueError(f"{block_id} selected_users invalid")
            return _unique(selected_users)
        return _split_tokens(pick_text(block_id))

    poll_targets = _unique(pick_users("poll_targets"))
    invitees: list[CalendarInvitee] = []
    for role, block_id in (
        ("required", "calendar_required"),
        ("optional", "calendar_optional"),
        ("excluded", "calendar_excluded"),
    ):
        invitees.extend(
            CalendarInvitee(value=token, role=role, kind=_invitee_kind(token))
            for token in pick_users(block_id)
        )
        invitees.extend(
            CalendarInvitee(value=token, role=role, kind="email")
            for token in _email_tokens(pick_text(f"{block_id}_emails"))
        )
    return poll_targets, _dedupe_invitees(invitees)


def _view_values(view: dict) -> dict:
    if not isinstance(view, dict):
        raise ValueError("view invalid")
    state = view.get("state")
    if not isinstance(state, dict):
        raise ValueError("view state missing")
    values = state.get("values")
    if not isinstance(values, dict):
        raise ValueError("view values missing")
    return values


def _block_values(values: dict, block_id: str) -> dict:
    block = values.get(block_id)
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ValueError(f"{block_id} block invalid")
    return block


def _element_values(block: dict, field: str) -> dict:
    elem = block.get(field) or block.get("value")
    if elem is None:
        return {}
    if not isinstance(elem, dict):
        raise ValueError("element invalid")
    return elem


def _validate_booking_url(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) > MAX_BOOKING_URL_LENGTH:
        raise ValueError("booking URL too long")
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ValueError("booking URL contains invalid characters")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("booking URL must be http(s) with host")
    return value


def _dedupe_invitees(invitees: list[CalendarInvitee]) -> list[CalendarInvitee]:
    latest: dict[tuple[str, str], CalendarInvitee] = {}
    order: list[tuple[str, str]] = []
    for invitee in invitees:
        key = (invitee.kind, invitee.value)
        if key not in latest:
            order.append(key)
        latest[key] = invitee
    return [latest[key] for key in order]


def _split_tokens(raw: str) -> list[str]:
    return [token for token in raw.replace(",", " ").split() if token]


def _email_tokens(raw: str) -> list[str]:
    return [_validate_email(token) for token in _split_tokens(raw)]


def _validate_email(value: str) -> str:
    if (
        not value
        or len(value) > MAX_EMAIL_LENGTH
        or any(ord(ch) < 32 for ch in value)
        or not EMAIL_RE.fullmatch(value)
    ):
        raise ValueError(f"invalid external email: {value}")
    return value


def _invitee_kind(value: str) -> str:
    return "email" if "@" in value else "slack"


def welcome_blocks() -> list[dict]:
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": m.WELCOME_TEXT},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": m.BTN_SETTINGS},
                    "action_id": "open_settings",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": m.BTN_STATUS},
                    "action_id": "show_status",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": m.BTN_START_NOW},
                    "action_id": "start_poll_now",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": m.BTN_CANCEL_RUN},
                    "action_id": "cancel_current_run",
                    "style": "danger",
                },
            ],
        },
    ]


def status_blocks(text: str) -> list[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": m.BTN_SETTINGS},
                    "action_id": "open_settings",
                    "style": "primary",
                },
            ],
        },
    ]
