# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import sessionmaker

from app.db.repository import ChannelRepository
from app.handlers.views import (
    loading_settings_modal,
    schedule_draft_from_spec,
    settings_modal,
)
from app.schedule.spec import ScheduleSpec
from app.settings_defaults import default_schedule_spec
from app.workflow.participants import (
    CalendarInvitee,
    ids_from_json,
    invitees_from_json,
    resolve_calendar_invitees,
    resolve_poll_target_ids,
)


def open_settings_modal(
    *,
    client,
    trigger_id: str,
    channel_id: str,
    session_factory: sessionmaker,
    member_lookup: Callable[[object, str], list[str]],
) -> None:
    opened = client.views_open(
        trigger_id=trigger_id,
        view=loading_settings_modal(channel_id),
    )
    view_id = (opened.get("view") or {}).get("id")
    spec = default_schedule_spec()
    poll_hours = None
    booking_url = None
    poll_target_ids = []
    calendar_invitees = []
    schedule_draft = None
    automatic_enabled = True
    current_member_ids = member_lookup(client, channel_id)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(channel_id)
        if ch and ch.schedule_json:
            spec = ScheduleSpec.model_validate_json(ch.schedule_json)
            poll_hours = ch.poll_duration_hours
            booking_url = ch.booking_url_template
            automatic_enabled = ch.automatic_execution_enabled
            if not automatic_enabled:
                schedule_draft = schedule_draft_from_spec(spec)
            known_member_ids = ids_from_json(ch.channel_member_ids_json)
            configured_poll_target_ids = ids_from_json(ch.poll_target_ids_json)
            configured_invitees = invitees_from_json(ch.calendar_invitees_json)
            drift_baseline_ids = known_member_ids or current_member_ids
            if ch.poll_target_ids_json is None:
                poll_target_ids = current_member_ids
            else:
                poll_target_ids = resolve_poll_target_ids(
                    configured_target_ids=configured_poll_target_ids,
                    known_member_ids=drift_baseline_ids,
                    current_member_ids=current_member_ids,
                )
            if ch.calendar_invitees_json is None:
                calendar_invitees = [
                    CalendarInvitee(value=user_id, role="required", kind="slack")
                    for user_id in current_member_ids
                ]
            else:
                calendar_invitees = resolve_calendar_invitees(
                    configured_invitees=configured_invitees,
                    known_member_ids=drift_baseline_ids,
                    current_member_ids=current_member_ids,
                )
    settings_view = settings_modal(
        channel_id,
        spec=spec,
        poll_duration_hours=poll_hours,
        booking_url=booking_url,
        poll_target_ids=poll_target_ids,
        calendar_invitees=calendar_invitees,
        automatic_enabled=automatic_enabled,
        schedule_draft=schedule_draft,
    )
    if view_id:
        client.views_update(view_id=view_id, view=settings_view)
