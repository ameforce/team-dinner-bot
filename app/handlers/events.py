# -*- coding: utf-8 -*-
from __future__ import annotations

from slack_bolt import App
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.config import settings
from app.db.repository import ChannelRepository
from app.schedule.spec import ScheduleSpec
from app.settings_defaults import default_schedule_spec
from app.handlers.intent import format_status
from app.handlers.intent import register_natural_language_handlers
from app.handlers.views import loading_settings_modal, settings_modal, status_blocks, welcome_blocks
from app.integrations.slack_users import list_human_member_ids
from app.scheduler.runner import JobScheduler
from app.workflow.engine import WorkflowEngine
from app.workflow.participants import (
    CalendarInvitee,
    ids_from_json,
    invitees_from_json,
    resolve_calendar_invitees,
    resolve_poll_target_ids,
)


def register_event_handlers(
    app: App,
    session_factory: sessionmaker,
    engine: WorkflowEngine | None = None,
    job_scheduler: JobScheduler | None = None,
) -> None:
    @app.event("member_joined_channel")
    def on_member_joined(event, client, logger):
        user_id = event.get("user")
        channel_id = event.get("channel")
        team_id = event.get("team") or event.get("team_id")

        auth = client.auth_test()
        if user_id != auth["user_id"]:
            return

        with session_factory() as session:
            ChannelRepository(session).upsert_on_bot_join(
                team_id=team_id or auth.get("team_id", ""),
                channel_id=channel_id,
            )

        client.chat_postMessage(
            channel=channel_id,
            text=m.WELCOME_TEXT,
            blocks=welcome_blocks(),
        )

    @app.event("member_left_channel")
    def on_member_left(event, client):
        user_id = event.get("user")
        channel_id = event.get("channel")
        if user_id != client.auth_test()["user_id"]:
            return
        with session_factory() as session:
            ChannelRepository(session).disable_channel(channel_id)
        if job_scheduler:
            job_scheduler.schedule_channel(channel_id)

    @app.action("open_settings")
    def open_settings(ack, body, client):
        ack()
        channel_id = body["channel"]["id"]
        opened = client.views_open(
            trigger_id=body["trigger_id"],
            view=loading_settings_modal(channel_id),
        )
        view_id = (opened.get("view") or {}).get("id")
        spec = default_schedule_spec()
        poll_hours = None
        booking_url = None
        poll_target_ids = []
        calendar_invitees = []
        current_member_ids = list_human_member_ids(client, channel_id)
        with session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(channel_id)
            if ch and ch.schedule_json:
                spec = ScheduleSpec.model_validate_json(ch.schedule_json)
                poll_hours = ch.poll_duration_hours
                booking_url = ch.booking_url_template
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
        )
        if view_id:
            client.views_update(view_id=view_id, view=settings_view)

    @app.action("show_status")
    def show_status(ack, body, client):
        ack()
        channel_id = body["channel"]["id"]
        text = format_status(channel_id, session_factory)
        client.chat_postEphemeral(
            channel=channel_id,
            user=body["user"]["id"],
            text=text,
            blocks=status_blocks(text),
        )

    @app.action("start_poll_now")
    def start_poll_now(ack, body, client):
        ack()
        channel_id = body["channel"]["id"]
        user_id = body["user"]["id"]
        if settings.admin_ids and user_id not in settings.admin_ids:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text=m.MSG_ADMIN_ONLY)
            return
        msg = engine.start_channel_run(channel_id, replace=False) if engine else m.MSG_CHANNEL_DISABLED
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=msg or m.MSG_POLL_START_REQUESTED,
        )

    @app.action("cancel_current_run")
    def cancel_current_run(ack, body, client):
        ack()
        channel_id = body["channel"]["id"]
        user_id = body["user"]["id"]
        if settings.admin_ids and user_id not in settings.admin_ids:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text=m.MSG_ADMIN_ONLY)
            return
        msg = engine.cancel_current_run(channel_id) if engine else m.MSG_CHANNEL_DISABLED
        client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)

    if engine is not None:
        register_natural_language_handlers(app, session_factory, engine, job_scheduler)
