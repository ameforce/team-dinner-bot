# -*- coding: utf-8 -*-
import pytest

from app.handlers.views import (
    MAX_BOOKING_URL_LENGTH,
    parse_automatic_execution_enabled,
    parse_participant_settings_submission,
    parse_settings_submission,
    settings_modal,
    status_blocks,
    welcome_blocks,
)
from app.workflow.participants import CalendarInvitee
from app.schedule.spec import ScheduleSpec, ScheduleType
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


def test_parse_monthly_day_settings_accepts_hidden_weekday_and_month_interval():
    spec, hours, _url = parse_settings_submission(
        _view_payload(
            schedule_type={
                "value": {"selected_option": {"value": "MONTHLY_DAY_OF_MONTH"}}
            },
            weekday={},
            day_of_month={"value": {"value": "15"}},
            month_interval={"value": {"value": "2"}},
        )
    )

    assert spec.type == ScheduleType.MONTHLY_DAY_OF_MONTH
    assert spec.day == 15
    assert spec.weekday is None
    assert spec.month_interval == 2
    assert hours == 24


def test_parse_automatic_execution_enabled_defaults_on_and_accepts_off():
    assert parse_automatic_execution_enabled(_view_payload()) is True

    view = _view_payload(
        automatic_execution={
            "value": {"selected_option": {"value": "off"}},
        }
    )

    assert parse_automatic_execution_enabled(view) is False


def _block_ids(view: dict) -> set[str]:
    return {block["block_id"] for block in view["blocks"] if "block_id" in block}


def _block_order(view: dict) -> list[str]:
    return [block["block_id"] for block in view["blocks"] if "block_id" in block]


def _block(view: dict, block_id: str) -> dict:
    return next(block for block in view["blocks"] if block.get("block_id") == block_id)


def test_settings_modal_auto_execution_off_hides_schedule_controls():
    view = settings_modal(
        "C1",
        spec=ScheduleSpec(
            type=ScheduleType.MONTHLY_DAY_OF_MONTH,
            day=15,
            month_interval=2,
            hour=10,
            minute=0,
        ),
        automatic_enabled=False,
    )

    ids = _block_ids(view)
    assert "automatic_execution" in ids
    assert "schedule_type" not in ids
    assert "weekday" not in ids
    assert "day_of_month" not in ids
    assert "nth" not in ids
    assert "month_interval" not in ids
    assert "poll_hour" not in ids
    assert "poll_duration" in ids
    assert "booking_url" in ids
    assert "poll_targets" in ids
    assert _block(view, "automatic_execution")["dispatch_action"] is True


def test_settings_modal_schedule_type_options_exclude_weekly():
    view = settings_modal("C1")

    options = _block(view, "schedule_type")["element"]["options"]
    assert [option["value"] for option in options] == [
        "MONTHLY_DAY_OF_MONTH",
        "MONTHLY_NTH_WEEKDAY",
    ]


def test_settings_modal_legacy_weekly_shows_notice_and_monthly_fallback():
    view = settings_modal(
        "C1",
        spec=ScheduleSpec(
            type=ScheduleType.WEEKLY_WEEKDAY,
            weekday=1,
            hour=10,
            minute=0,
        ),
    )

    ids = _block_ids(view)
    assert "legacy_weekly_notice" in ids
    assert "month_interval" in ids
    assert "day_of_month" in ids
    assert "weekday" not in ids
    assert "nth" not in ids
    schedule_block = _block(view, "schedule_type")
    assert schedule_block["dispatch_action"] is True
    assert schedule_block["element"]["initial_option"]["value"] == "MONTHLY_DAY_OF_MONTH"
    assert "WEEKLY_WEEKDAY" not in [option["value"] for option in schedule_block["element"]["options"]]


def test_settings_modal_draft_metadata_is_versioned():
    view = settings_modal(
        "C1",
        spec=ScheduleSpec(
            type=ScheduleType.MONTHLY_DAY_OF_MONTH,
            day=15,
            month_interval=2,
            hour=10,
            minute=0,
        ),
        schedule_draft={
            "type": "MONTHLY_DAY_OF_MONTH",
            "day": "15",
            "month_interval": "2",
        },
    )

    metadata = view["private_metadata"]
    assert '"version":1' in metadata
    assert '"channel_id":"C1"' in metadata
    assert '"schedule_draft"' in metadata


def test_settings_modal_monthly_day_shows_day_and_interval_only():
    view = settings_modal(
        "C1",
        spec=ScheduleSpec(
            type=ScheduleType.MONTHLY_DAY_OF_MONTH,
            day=15,
            month_interval=2,
            hour=10,
            minute=0,
        ),
        automatic_enabled=True,
    )

    ids = _block_ids(view)
    assert "automatic_execution" in ids
    assert "day_of_month" in ids
    assert "month_interval" in ids
    assert "weekday" not in ids
    assert "nth" not in ids
    auto_block = next(block for block in view["blocks"] if block.get("block_id") == "automatic_execution")
    assert auto_block["element"]["initial_option"]["value"] == "on"


def test_settings_modal_monthly_day_block_order_and_generic_date_label():
    view = settings_modal(
        "C1",
        spec=ScheduleSpec(
            type=ScheduleType.MONTHLY_DAY_OF_MONTH,
            day=15,
            month_interval=2,
            hour=10,
            minute=0,
        ),
    )

    order = _block_order(view)
    assert order.index("month_interval") < order.index("day_of_month") < order.index("poll_hour")
    assert _block(view, "day_of_month")["label"]["text"] == "날짜 (1–28)"


def test_settings_modal_monthly_nth_shows_weekday_nth_and_interval():
    view = settings_modal(
        "C1",
        spec=ScheduleSpec(
            type=ScheduleType.MONTHLY_NTH_WEEKDAY,
            weekday=1,
            nth=2,
            month_interval=3,
            hour=10,
            minute=0,
        ),
    )

    ids = _block_ids(view)
    assert "weekday" in ids
    assert "nth" in ids
    assert "month_interval" in ids
    assert "day_of_month" not in ids


def test_settings_modal_monthly_nth_block_order():
    view = settings_modal(
        "C1",
        spec=ScheduleSpec(
            type=ScheduleType.MONTHLY_NTH_WEEKDAY,
            weekday=1,
            nth=2,
            month_interval=3,
            hour=10,
            minute=0,
        ),
    )

    order = _block_order(view)
    assert order.index("month_interval") < order.index("nth") < order.index("weekday") < order.index("poll_hour")


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
        CalendarInvitee(value="U3", role="optional", kind="slack"),
    ]


def test_parse_participant_settings_ignores_legacy_external_email_fields():
    view = _view_payload(calendar_required_emails={"value": {"value": "not-an-email"}})

    _poll_targets, invitees = parse_participant_settings_submission(view)

    assert invitees == []


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
    assert "calendar_required_emails" not in blocks
    assert "calendar_optional_emails" not in blocks
    assert "calendar_excluded" not in blocks
    assert "calendar_excluded_emails" not in blocks


def test_settings_modal_does_not_render_separate_poll_target_summary():
    selected = [f"U{i}" for i in range(1, 23)]

    view = settings_modal("C1", poll_target_ids=selected)

    blocks = {block["block_id"]: block for block in view["blocks"] if "block_id" in block}
    assert blocks["poll_targets"]["element"]["type"] == "multi_users_select"
    assert blocks["poll_targets"]["element"]["initial_users"] == selected
    assert "poll_targets_summary" not in blocks


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
