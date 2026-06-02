# -*- coding: utf-8 -*-
"""Modal submit and other non-slash handlers."""

from __future__ import annotations

from slack_bolt import App
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.db.repository import ChannelRepository
from app.handlers.views import parse_participant_settings_submission, parse_settings_submission
from app.integrations.slack_users import list_human_member_ids
from app.scheduler.runner import JobScheduler
from app.workflow.engine import WorkflowEngine


def register_command_handlers(
    app: App,
    session_factory: sessionmaker,
    engine: WorkflowEngine,
    job_scheduler: JobScheduler | None = None,
) -> None:
    @app.view("settings_submit")
    def on_settings_submit(ack, body, client, view):
        ack()
        channel_id = _settings_channel_id(view)
        if not channel_id:
            return
        try:
            spec, poll_hours, booking_url = parse_settings_submission(view)
            poll_target_ids, calendar_invitees = parse_participant_settings_submission(view)
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
            )

        if job_scheduler:
            job_scheduler.schedule_channel(channel_id)

        client.chat_postMessage(
            channel=channel_id,
            text=f"{m.MSG_SAVED} {spec.describe_ko()} (\ud22c\ud45c {poll_hours}\uc2dc\uac04)",
        )


def _settings_channel_id(view: dict) -> str | None:
    if not isinstance(view, dict):
        return None
    channel_id = view.get("private_metadata")
    if not isinstance(channel_id, str):
        return None
    channel_id = channel_id.strip()
    return channel_id or None


def _settings_team_id(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    team = body.get("team") or {}
    if isinstance(team, dict):
        team_id = team.get("id")
        if isinstance(team_id, str):
            return team_id.strip()
    return ""
