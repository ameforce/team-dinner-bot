# -*- coding: utf-8 -*-
"""Modal submit and other non-slash handlers."""

from __future__ import annotations

from slack_bolt import App
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.db.repository import ChannelRepository
from app.handlers.views import (
    decode_settings_metadata,
    parse_automatic_execution_enabled,
    parse_non_schedule_settings_submission,
    parse_participant_settings_submission,
    parse_settings_submission,
    schedule_spec_from_draft,
    welcome_blocks,
)
from app.handlers.intent import dispatch_hoesik_intent
from app.handlers.settings_modal_flow import open_settings_modal
from app.integrations.slack_users import list_human_member_ids
from app.schedule.spec import ScheduleSpec
from app.settings_defaults import default_schedule_spec
from app.scheduler.runner import JobScheduler
from app.workflow.engine import WorkflowEngine


def register_command_handlers(
    app: App,
    session_factory: sessionmaker,
    engine: WorkflowEngine,
    job_scheduler: JobScheduler | None = None,
) -> None:
    @app.command(f"/{m.USER_CMD}")
    def on_hoesik_slash_command(ack, body, client, logger=None):
        ack()
        channel_id = _slash_channel_id(body)
        user_id = _slash_user_id(body)
        sub_text = _slash_text(body)
        if not channel_id or not user_id:
            return

        if _is_settings_subcommand(sub_text):
            trigger_id = _slash_trigger_id(body)
            if not trigger_id:
                client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=m.MSG_USE_SETTINGS_BUTTON,
                )
                return
            open_settings_modal(
                client=client,
                trigger_id=trigger_id,
                channel_id=channel_id,
                session_factory=session_factory,
                member_lookup=list_human_member_ids,
            )
            return

        dispatch_hoesik_intent(
            sub_text=sub_text,
            channel_id=channel_id,
            user_id=user_id,
            session_factory=session_factory,
            engine=engine,
            job_scheduler=job_scheduler,
            reply=lambda msg: client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=msg,
            ),
            open_modal=None,
            post_action_prompt=lambda: client.chat_postMessage(
                channel=channel_id,
                text=m.MSG_SETTINGS_PROMPT,
                blocks=welcome_blocks(),
            ),
        )

    @app.view("settings_submit")
    def on_settings_submit(ack, body, client, view):
        ack()
        channel_id, schedule_draft = _settings_context(view)
        if not channel_id:
            return
        try:
            automatic_enabled = parse_automatic_execution_enabled(view)
            poll_target_ids, calendar_invitees = parse_participant_settings_submission(view)
            if automatic_enabled:
                spec, poll_hours, booking_url = parse_settings_submission(view)
            else:
                try:
                    spec, poll_hours, booking_url = parse_settings_submission(view)
                except (ValueError, TypeError):
                    poll_hours, booking_url = parse_non_schedule_settings_submission(view)
                    spec = schedule_spec_from_draft(schedule_draft) if schedule_draft else None
        except (ValueError, TypeError):
            client.chat_postMessage(channel=channel_id, text=m.MSG_SETTINGS_INVALID)
            return

        team_id = _settings_team_id(body)
        with session_factory() as session:
            repo = ChannelRepository(session)
            row = repo.get_by_channel_id(channel_id)
            if (not row or not row.team_id.strip()) and not team_id:
                client.chat_postMessage(channel=channel_id, text=m.MSG_SETTINGS_INVALID)
                return
            if spec is None:
                spec = _existing_schedule_or_default(row)

        current_member_ids = list_human_member_ids(client, channel_id)
        with session_factory() as session:
            repo = ChannelRepository(session)
            repo.save_channel_settings(
                channel_id,
                team_id=team_id,
                spec=spec,
                poll_duration_hours=poll_hours,
                booking_url=booking_url,
                poll_target_ids=poll_target_ids,
                calendar_invitees=calendar_invitees,
                channel_member_ids=current_member_ids,
                automatic_execution_enabled=automatic_enabled,
            )

        if job_scheduler:
            job_scheduler.schedule_channel(channel_id)

        client.chat_postMessage(
            channel=channel_id,
            text=f"{m.MSG_SAVED} {spec.describe_ko()} (\ud22c\ud45c {poll_hours}\uc2dc\uac04)",
        )


def _slash_channel_id(body: dict) -> str:
    channel_id = body.get("channel_id") if isinstance(body, dict) else None
    if isinstance(channel_id, str):
        return channel_id.strip()
    channel = body.get("channel") if isinstance(body, dict) else None
    if isinstance(channel, dict):
        value = channel.get("id")
        if isinstance(value, str):
            return value.strip()
    return ""


def _slash_user_id(body: dict) -> str:
    user_id = body.get("user_id") if isinstance(body, dict) else None
    if isinstance(user_id, str):
        return user_id.strip()
    user = body.get("user") if isinstance(body, dict) else None
    if isinstance(user, dict):
        value = user.get("id")
        if isinstance(value, str):
            return value.strip()
    return ""


def _slash_text(body: dict) -> str:
    text = body.get("text") if isinstance(body, dict) else None
    return text.strip() if isinstance(text, str) else ""


def _slash_trigger_id(body: dict) -> str:
    trigger_id = body.get("trigger_id") if isinstance(body, dict) else None
    return trigger_id.strip() if isinstance(trigger_id, str) else ""


def _is_settings_subcommand(sub_text: str) -> bool:
    parts = sub_text.split()
    return bool(parts and parts[0].lower() in {"settings", "setting", "설정"})


def _settings_context(view: dict) -> tuple[str | None, dict]:
    if not isinstance(view, dict):
        return None, {}
    return decode_settings_metadata(view.get("private_metadata"))


def _settings_team_id(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    team = body.get("team") or {}
    if isinstance(team, dict):
        team_id = team.get("id")
        if isinstance(team_id, str):
            return team_id.strip()
    return ""


def _existing_schedule_or_default(row) -> ScheduleSpec:
    if row and row.schedule_json:
        return ScheduleSpec.model_validate_json(row.schedule_json)
    return default_schedule_spec()
