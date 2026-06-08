# -*- coding: utf-8 -*-
"""Slack modals for channel settings."""

from __future__ import annotations

import json
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
    {"text": {"type": "plain_text", "text": m.OPT_MONTHLY_DAY}, "value": "MONTHLY_DAY_OF_MONTH"},
    {"text": {"type": "plain_text", "text": m.OPT_MONTHLY_NTH}, "value": "MONTHLY_NTH_WEEKDAY"},
]

AUTOMATIC_EXECUTION_OPTIONS = [
    {"text": {"type": "plain_text", "text": m.OPT_AUTOMATIC_ON}, "value": "on"},
    {"text": {"type": "plain_text", "text": m.OPT_AUTOMATIC_OFF}, "value": "off"},
]

MAX_BOOKING_URL_LENGTH = 2048
SETTINGS_METADATA_SCHEDULE_DRAFT = "schedule_draft"

WEEKDAY_OPTIONS = [
    {"text": {"type": "plain_text", "text": label}, "value": str(i)}
    for i, label in enumerate(m.WEEKDAYS)
]


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
    automatic_enabled: bool = True,
    schedule_draft: dict | None = None,
) -> dict:
    spec = spec or default_schedule_spec()
    if schedule_draft is not None:
        spec = schedule_spec_from_draft(schedule_draft, fallback=spec)
    legacy_weekly = spec.type == ScheduleType.WEEKLY_WEEKDAY
    if legacy_weekly:
        spec = ScheduleSpec(
            type=ScheduleType.MONTHLY_DAY_OF_MONTH,
            day=15,
            month_interval=1,
            hour=spec.hour,
            minute=spec.minute,
        )
    poll_hours = clamp_poll_duration_hours(poll_duration_hours)
    booking_url = booking_url or ""
    poll_target_ids = poll_target_ids or []
    calendar_invitees = calendar_invitees or []

    automatic_initial = _initial_select(
        AUTOMATIC_EXECUTION_OPTIONS,
        "on" if automatic_enabled else "off",
    )
    type_initial = _initial_select(SCHEDULE_TYPE_OPTIONS, spec.type.value)
    weekday_initial = _initial_select(WEEKDAY_OPTIONS, str(spec.weekday if spec.weekday is not None else 1))

    automatic_elem: dict = {
        "type": "static_select",
        "action_id": "value",
        "options": AUTOMATIC_EXECUTION_OPTIONS,
    }
    if automatic_initial:
        automatic_elem["initial_option"] = automatic_initial

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

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": m.MODAL_HELP},
        },
        {
            "type": "input",
            "block_id": "automatic_execution",
            "dispatch_action": True,
            "label": {"type": "plain_text", "text": m.LABEL_AUTOMATIC_EXECUTION},
            "element": automatic_elem,
        },
    ]
    if automatic_enabled:
        if legacy_weekly:
            blocks.append(
                {
                    "type": "context",
                    "block_id": "legacy_weekly_notice",
                    "elements": [{"type": "mrkdwn", "text": m.LEGACY_WEEKLY_NOTICE}],
                }
            )
        blocks.append(
            {
                "type": "input",
                "block_id": "schedule_type",
                "dispatch_action": True,
                "label": {"type": "plain_text", "text": m.LABEL_SCHEDULE_TYPE},
                "element": schedule_elem,
            }
        )
        blocks.extend(_schedule_detail_blocks(spec, weekday_elem))
        blocks.append(
            {
                "type": "input",
                "block_id": "poll_hour",
                "label": {"type": "plain_text", "text": m.LABEL_HOUR},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(spec.hour),
                },
            }
        )
    blocks.extend(
        [
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
                "block_id": "calendar_optional",
                "optional": True,
                "label": {"type": "plain_text", "text": "캘린더 선택 초대"},
                "element": _multi_users_element(
                    _invitee_values(calendar_invitees, "optional", "slack"),
                    "선택 초대자 선택",
                ),
            },
        ]
    )

    return {
        "type": "modal",
        "callback_id": "settings_submit",
        "private_metadata": encode_settings_metadata(channel_id, schedule_draft),
        "title": {"type": "plain_text", "text": m.MODAL_TITLE},
        "submit": {"type": "plain_text", "text": m.MODAL_SAVE},
        "close": {"type": "plain_text", "text": m.MODAL_CANCEL},
        "blocks": blocks,
    }


def _schedule_detail_blocks(spec: ScheduleSpec, weekday_elem: dict) -> list[dict]:
    blocks: list[dict] = []
    if spec.type in {ScheduleType.MONTHLY_DAY_OF_MONTH, ScheduleType.MONTHLY_NTH_WEEKDAY}:
        blocks.append(
            {
                "type": "input",
                "block_id": "month_interval",
                "optional": True,
                "label": {"type": "plain_text", "text": m.LABEL_MONTH_INTERVAL},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "value",
                    "initial_value": str(spec.month_interval),
                    "placeholder": {"type": "plain_text", "text": "1"},
                },
            }
        )
    if spec.type == ScheduleType.MONTHLY_DAY_OF_MONTH:
        blocks.append(
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
            }
        )
    if spec.type == ScheduleType.MONTHLY_NTH_WEEKDAY:
        blocks.append(
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
            }
        )
    if spec.type == ScheduleType.MONTHLY_NTH_WEEKDAY:
        blocks.append(
            {
                "type": "input",
                "block_id": "weekday",
                "optional": True,
                "label": {"type": "plain_text", "text": m.LABEL_WEEKDAY},
                "element": weekday_elem,
            }
        )
    return blocks


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


def encode_settings_metadata(channel_id: str, schedule_draft: dict | None = None) -> str:
    if not schedule_draft:
        return channel_id
    return json.dumps(
        {
            "version": 1,
            "channel_id": channel_id,
            SETTINGS_METADATA_SCHEDULE_DRAFT: schedule_draft,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_settings_metadata(raw: object) -> tuple[str | None, dict]:
    if not isinstance(raw, str):
        return None, {}
    raw = raw.strip()
    if not raw:
        return None, {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, {}
    if not isinstance(payload, dict):
        return raw, {}
    channel_id = payload.get("channel_id")
    schedule_draft = payload.get(SETTINGS_METADATA_SCHEDULE_DRAFT)
    return (
        channel_id.strip() if isinstance(channel_id, str) and channel_id.strip() else None,
        schedule_draft if isinstance(schedule_draft, dict) else {},
    )


def schedule_draft_from_view(
    view: dict,
    *,
    previous_draft: dict | None = None,
    selected_type: str | None = None,
) -> dict:
    values = _view_values(view)
    draft = dict(previous_draft or {})
    if selected_type:
        draft["type"] = selected_type
    for block_id, key in (
        ("schedule_type", "type"),
        ("weekday", "weekday"),
        ("day_of_month", "day"),
        ("nth", "nth"),
        ("month_interval", "month_interval"),
        ("poll_hour", "hour"),
    ):
        raw = _pick_value(values, block_id)
        if raw not in (None, ""):
            draft[key] = raw
    return draft


def schedule_draft_from_spec(spec: ScheduleSpec) -> dict:
    draft: dict[str, int | str] = {
        "type": spec.type.value,
        "hour": spec.hour,
    }
    if spec.weekday is not None:
        draft["weekday"] = spec.weekday
    if spec.day is not None:
        draft["day"] = spec.day
    if spec.nth is not None:
        draft["nth"] = spec.nth
    draft["month_interval"] = spec.month_interval
    return draft


def schedule_spec_from_draft(
    draft: dict,
    *,
    fallback: ScheduleSpec | None = None,
) -> ScheduleSpec:
    fallback = fallback or default_schedule_spec()
    schedule_type = ScheduleType(str(draft.get("type") or fallback.type.value))
    spec_kwargs: dict = {
        "type": schedule_type,
        "hour": fallback.hour,
        "minute": fallback.minute,
    }
    for key in ("weekday", "day", "nth", "month_interval", "hour"):
        value = _int_or_none(draft.get(key))
        if value is not None:
            spec_kwargs[key] = value
    if schedule_type == ScheduleType.WEEKLY_WEEKDAY:
        spec_kwargs.setdefault("weekday", fallback.weekday if fallback.weekday is not None else 1)
    elif schedule_type == ScheduleType.MONTHLY_DAY_OF_MONTH:
        spec_kwargs.setdefault("day", fallback.day if fallback.day is not None else 15)
        spec_kwargs.setdefault("month_interval", fallback.month_interval)
    elif schedule_type == ScheduleType.MONTHLY_NTH_WEEKDAY:
        spec_kwargs.setdefault("weekday", fallback.weekday if fallback.weekday is not None else 1)
        spec_kwargs.setdefault("nth", fallback.nth if fallback.nth is not None else 2)
        spec_kwargs.setdefault("month_interval", fallback.month_interval)
    return ScheduleSpec(**spec_kwargs)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value)
    return None


def parse_automatic_execution_enabled(view: dict) -> bool:
    values = _view_values(view)
    raw = _pick_value(values, "automatic_execution")
    if raw is None:
        return True
    if raw == "on":
        return True
    if raw == "off":
        return False
    raise ValueError("automatic_execution invalid")


def parse_settings_submission(view: dict) -> tuple[ScheduleSpec, int, str | None]:
    values = _view_values(view)

    schedule_type = _pick_value(values, "schedule_type")
    if not schedule_type:
        raise ValueError("schedule_type required")

    weekday_raw = _pick_value(values, "weekday")
    day_raw = _pick_value(values, "day_of_month")
    nth_raw = _pick_value(values, "nth")
    month_interval_raw = _pick_value(values, "month_interval")
    hour_raw = _pick_value(values, "poll_hour") or "10"
    duration_raw = _pick_value(values, "poll_duration") or str(DEFAULT_POLL_DURATION_HOURS)
    booking_url = _validate_booking_url((_pick_value(values, "booking_url") or "").strip() or None)

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
    if month_interval_raw:
        spec_kwargs["month_interval"] = int(month_interval_raw)

    if spec_kwargs["type"] == ScheduleType.WEEKLY_WEEKDAY:
        spec_kwargs.setdefault("weekday", 1)
    elif spec_kwargs["type"] == ScheduleType.MONTHLY_DAY_OF_MONTH:
        spec_kwargs.setdefault("day", 15)
        spec_kwargs.setdefault("month_interval", 1)
    elif spec_kwargs["type"] == ScheduleType.MONTHLY_NTH_WEEKDAY:
        spec_kwargs.setdefault("weekday", 1)
        spec_kwargs.setdefault("nth", 2)
        spec_kwargs.setdefault("month_interval", 1)

    return ScheduleSpec(**spec_kwargs), poll_duration, booking_url


def parse_non_schedule_settings_submission(view: dict) -> tuple[int, str | None]:
    values = _view_values(view)
    duration_raw = _pick_value(values, "poll_duration") or str(DEFAULT_POLL_DURATION_HOURS)
    booking_url = _validate_booking_url((_pick_value(values, "booking_url") or "").strip() or None)
    return clamp_poll_duration_hours(int(duration_raw)), booking_url


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
    ):
        invitees.extend(
            CalendarInvitee(value=token, role=role, kind="slack")
            for token in pick_users(block_id)
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


def _pick_value(values: dict, block_id: str, field: str = "value") -> str | None:
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
