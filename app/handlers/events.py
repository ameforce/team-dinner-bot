# -*- coding: utf-8 -*-
from __future__ import annotations

from slack_bolt import App
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.config import settings
from app.db.repository import ChannelRepository
from app.schedule.spec import ScheduleSpec, ScheduleType
from app.handlers.intent import format_status
from app.handlers.settings_modal_flow import open_settings_modal
from app.handlers.views import (
    decode_settings_metadata,
    parse_automatic_execution_enabled,
    parse_participant_settings_submission,
    parse_non_schedule_settings_submission,
    parse_settings_submission,
    schedule_draft_from_view,
    schedule_spec_from_draft,
    settings_modal,
    status_blocks,
    welcome_blocks,
)
from app.integrations.slack_users import list_human_member_ids
from app.scheduler.runner import JobScheduler
from app.workflow.engine import WorkflowEngine


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
        open_settings_modal(
            client=client,
            trigger_id=body["trigger_id"],
            channel_id=channel_id,
            session_factory=session_factory,
            member_lookup=list_human_member_ids,
        )

    @app.action("value")
    def on_settings_value_change(ack, body, client):
        ack()
        action = _first_action(body)
        block_id = action.get("block_id")
        if block_id not in {"schedule_type", "automatic_execution"}:
            return
        source_view = body.get("view") if isinstance(body, dict) else None
        if not isinstance(source_view, dict):
            return
        channel_id, previous_draft = decode_settings_metadata(source_view.get("private_metadata"))
        if not channel_id:
            return
        selected_type = _selected_schedule_type(action) if block_id == "schedule_type" else None
        try:
            automatic_enabled = parse_automatic_execution_enabled(source_view)
        except (ValueError, TypeError):
            automatic_enabled = True
        try:
            schedule_draft = schedule_draft_from_view(
                source_view,
                previous_draft=previous_draft,
                selected_type=selected_type,
            )
            spec = schedule_spec_from_draft(schedule_draft)
        except (ValueError, TypeError):
            try:
                spec = _default_spec_for_type(ScheduleType(selected_type))
            except (TypeError, ValueError):
                return
            schedule_draft = {"type": spec.type.value}
        try:
            _spec, poll_hours, booking_url = parse_settings_submission(source_view)
        except (ValueError, TypeError):
            try:
                poll_hours, booking_url = parse_non_schedule_settings_submission(source_view)
            except (ValueError, TypeError):
                poll_hours = None
                booking_url = None
        try:
            poll_target_ids, calendar_invitees = parse_participant_settings_submission(source_view)
        except (ValueError, TypeError):
            poll_target_ids = []
            calendar_invitees = []
        settings_view = settings_modal(
            channel_id.strip(),
            spec=spec,
            poll_duration_hours=poll_hours,
            booking_url=booking_url,
            poll_target_ids=poll_target_ids,
            calendar_invitees=calendar_invitees,
            automatic_enabled=automatic_enabled,
            schedule_draft=schedule_draft,
        )
        view_id = source_view.get("id")
        if not isinstance(view_id, str) or not view_id:
            return
        kwargs = {"view_id": view_id, "view": settings_view}
        view_hash = source_view.get("hash")
        if isinstance(view_hash, str) and view_hash:
            kwargs["hash"] = view_hash
        client.views_update(**kwargs)

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


def _first_action(body: dict) -> dict:
    actions = body.get("actions") if isinstance(body, dict) else None
    if not isinstance(actions, list) or not actions:
        return {}
    action = actions[0]
    return action if isinstance(action, dict) else {}


def _selected_schedule_type(action: dict) -> str | None:
    selected_option = action.get("selected_option")
    if not isinstance(selected_option, dict):
        return None
    value = selected_option.get("value")
    return value if isinstance(value, str) and value else None


def _default_spec_for_type(schedule_type: ScheduleType) -> ScheduleSpec:
    if schedule_type == ScheduleType.WEEKLY_WEEKDAY:
        return ScheduleSpec(type=schedule_type, weekday=1, hour=10, minute=0)
    if schedule_type == ScheduleType.MONTHLY_DAY_OF_MONTH:
        return ScheduleSpec(type=schedule_type, day=15, hour=10, minute=0)
    if schedule_type == ScheduleType.MONTHLY_NTH_WEEKDAY:
        return ScheduleSpec(type=schedule_type, weekday=1, nth=2, hour=10, minute=0)
    raise ValueError(f"unsupported schedule type: {schedule_type}")
