# -*- coding: utf-8 -*-
import pytest

from app.handlers.views import (
    MAX_BOOKING_URL_LENGTH,
    MAX_EMAIL_LENGTH,
    parse_participant_settings_submission,
    parse_settings_submission,
    settings_modal,
    status_blocks,
    welcome_blocks,
)
from app.workflow.participants import CalendarInvitee
from app.schedule.spec import ScheduleType
from app.settings_defaults import DEFAULT_POLL_DURATION_HOURS, MIN_POLL_DURATION_HOURS


def _view_payload(**overrides):
    base = {
        "state": {
            "values": {
                "schedule_type": {
                    "value": {"selected_option": {"value": "WEEKLY_WEEKDAY"}}
                },
                "weekday": {"value": {"selected_option": {"value": "1"}}},
                "poll_hour": {"value": {"value": "14"}},
                "poll_duration": {"value": {"value": "24"}},
            }
        }
    }
    base["state"]["values"].update(overrides)
    return base


def test_parse_weekly_settings():
    spec, hours, url = parse_settings_submission(_view_payload())
    assert spec.type == ScheduleType.WEEKLY_WEEKDAY
    assert spec.weekday == 1
    assert spec.hour == 14
    assert hours == 24


def test_parse_booking_url_accepts_https():
    _spec, _hours, url = parse_settings_submission(
        _view_payload(booking_url={"value": {"value": "https://example.com/book?room=4"}})
    )

    assert url == "https://example.com/book?room=4"


def test_parse_booking_url_rejects_unsafe_scheme():
    with pytest.raises(ValueError):
        parse_settings_submission(
            _view_payload(booking_url={"value": {"value": "javascript:alert(1)"}})
        )


def test_parse_booking_url_rejects_missing_host():
    with pytest.raises(ValueError):
        parse_settings_submission(_view_payload(booking_url={"value": {"value": "https://"}}))


def test_parse_booking_url_rejects_too_long_value():
    prefix = "https://example.com/"
    too_long = prefix + ("a" * (MAX_BOOKING_URL_LENGTH - len(prefix) + 1))

    with pytest.raises(ValueError):
        parse_settings_submission(_view_payload(booking_url={"value": {"value": too_long}}))


def test_parse_settings_submission_rejects_malformed_state():
    with pytest.raises(ValueError):
        parse_settings_submission({"state": {}})


def test_parse_clamps_short_poll_duration():
    _spec, hours, url = parse_settings_submission(
        _view_payload(poll_duration={"value": {"value": "1"}})
    )
    assert hours == MIN_POLL_DURATION_HOURS
    assert url is None
    assert url is None


def test_parse_participant_settings_submission_splits_poll_and_calendar_roles():
    view = _view_payload(
        poll_targets={"value": {"selected_users": ["U1", "U2"]}},
        calendar_required={"value": {"selected_users": ["U1"]}},
        calendar_required_emails={"value": {"value": "required@example.com"}},
        calendar_optional={"value": {"selected_users": ["U3"]}},
        calendar_optional_emails={"value": {"value": "optional@example.com"}},
        calendar_excluded={"value": {"selected_users": ["U2"]}},
    )

    poll_targets, invitees = parse_participant_settings_submission(view)

    assert poll_targets == ["U1", "U2"]
    assert invitees == [
        CalendarInvitee(value="U1", role="required", kind="slack"),
        CalendarInvitee(value="required@example.com", role="required", kind="email"),
        CalendarInvitee(value="U3", role="optional", kind="slack"),
        CalendarInvitee(value="optional@example.com", role="optional", kind="email"),
        CalendarInvitee(value="U2", role="excluded", kind="slack"),
    ]


def test_parse_participant_settings_rejects_malformed_external_email():
    view = _view_payload(calendar_required_emails={"value": {"value": "not-an-email"}})

    with pytest.raises(ValueError):
        parse_participant_settings_submission(view)


def test_parse_participant_settings_rejects_too_long_external_email():
    local = "a" * (MAX_EMAIL_LENGTH - len("@example.com") + 1)
    view = _view_payload(calendar_required_emails={"value": {"value": f"{local}@example.com"}})

    with pytest.raises(ValueError):
        parse_participant_settings_submission(view)


def test_parse_participant_settings_preserves_selected_slack_users():
    view = _view_payload(calendar_required={"value": {"selected_users": ["U1"]}})

    _poll_targets, invitees = parse_participant_settings_submission(view)

    assert invitees == [CalendarInvitee(value="U1", role="required", kind="slack")]


def test_parse_participant_settings_rejects_malformed_selected_users():
    view = _view_payload(calendar_required={"value": {"selected_users": "U1"}})

    with pytest.raises(ValueError):
        parse_participant_settings_submission(view)


def test_settings_modal_prefills_participant_fields():
    view = settings_modal(
        "C1",
        poll_target_ids=["U1", "U2"],
        calendar_invitees=[
            CalendarInvitee(value="U1", role="required", kind="slack"),
            CalendarInvitee(value="partner@example.com", role="optional", kind="email"),
            CalendarInvitee(value="U2", role="excluded", kind="slack"),
        ],
    )

    blocks = {block["block_id"]: block for block in view["blocks"] if "block_id" in block}
    assert blocks["poll_targets"]["element"]["type"] == "multi_users_select"
    assert blocks["poll_targets"]["element"]["initial_users"] == ["U1", "U2"]
    assert blocks["calendar_required"]["element"]["type"] == "multi_users_select"
    assert blocks["calendar_required"]["element"]["initial_users"] == ["U1"]
    assert blocks["calendar_optional"]["element"]["type"] == "multi_users_select"
    assert "initial_users" not in blocks["calendar_optional"]["element"]
    assert blocks["calendar_excluded"]["element"]["type"] == "multi_users_select"
    assert blocks["calendar_excluded"]["element"]["initial_users"] == ["U2"]
    assert blocks["calendar_optional_emails"]["element"]["initial_value"] == "partner@example.com"


def test_welcome_blocks_have_actions():
    blocks = welcome_blocks()
    action_ids = [
        el["action_id"]
        for b in blocks
        if b["type"] == "actions"
        for el in b["elements"]
    ]
    assert "open_settings" in action_ids
    assert "show_status" in action_ids
    assert "start_poll_now" in action_ids
    assert "cancel_current_run" in action_ids


def test_status_blocks_include_settings_action():
    blocks = status_blocks("*status*")
    action_ids = [
        el["action_id"]
        for b in blocks
        if b["type"] == "actions"
        for el in b["elements"]
    ]
    assert "open_settings" in action_ids


def test_default_poll_duration_is_24_hours():
    assert DEFAULT_POLL_DURATION_HOURS == 24
