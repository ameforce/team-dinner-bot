# -*- coding: utf-8 -*-
from __future__ import annotations

from app.workflow.participants import (
    CalendarInvitee,
    resolve_calendar_invitees,
    resolve_poll_target_ids,
)


def test_poll_targets_preserve_existing_settings_and_include_only_new_members_by_default():
    targets = resolve_poll_target_ids(
        configured_target_ids=["U1"],
        known_member_ids=["U1", "U2"],
        current_member_ids=["U1", "U2", "U3"],
    )

    assert targets == ["U1", "U3"]


def test_calendar_invitees_keep_only_required_optional_slack_and_default_new_members():
    invitees = resolve_calendar_invitees(
        configured_invitees=[
            CalendarInvitee(value="U1", role="required", kind="slack"),
            CalendarInvitee(value="U2", role="excluded", kind="slack"),
            CalendarInvitee(value="partner@example.com", role="optional", kind="email"),
        ],
        known_member_ids=["U1", "U2"],
        current_member_ids=["U1", "U2", "U3"],
    )

    assert invitees == [
        CalendarInvitee(value="U1", role="required", kind="slack"),
        CalendarInvitee(value="U3", role="required", kind="slack"),
    ]
