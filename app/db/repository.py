# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AssigneeHistory, Channel, PollVote, WorkflowRun
from app.schedule.spec import ScheduleSpec
from app.settings_defaults import (
    DEFAULT_POLL_DURATION_HOURS,
    clamp_poll_duration_hours,
    default_schedule_spec,
)
from app.workflow.participants import CalendarInvitee, ids_to_json, invitees_to_json

_POLL_OPEN_STATES = ("POLL_OPEN", "REMIND_POSTED")
_ACTIVE_STATES = ("POLL_OPEN", "REMIND_POSTED", "POLL_CLOSED", "BOOKING_ASSIGNED")


class ChannelRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_channel_id(self, channel_id: str) -> Channel | None:
        return self.session.scalar(select(Channel).where(Channel.channel_id == channel_id))

    def list_enabled_with_schedule(self) -> list[Channel]:
        rows = self.session.scalars(select(Channel).where(Channel.enabled.is_(True))).all()
        return [r for r in rows if r.schedule_json]

    def upsert_on_bot_join(self, team_id: str, channel_id: str) -> Channel:
        team_id = team_id.strip()
        row = self.get_by_channel_id(channel_id)
        if row:
            row.enabled = True
            if team_id:
                row.team_id = team_id
            elif not row.team_id.strip():
                raise ValueError(f"Team id required for channel: {channel_id}")
            self.session.commit()
            return row
        if not team_id:
            raise ValueError(f"Team id required for new channel: {channel_id}")
        row = Channel(
            team_id=team_id,
            channel_id=channel_id,
            enabled=True,
            schedule_json=default_schedule_spec().model_dump_json(),
            poll_duration_hours=DEFAULT_POLL_DURATION_HOURS,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def disable_channel(self, channel_id: str) -> None:
        row = self.get_by_channel_id(channel_id)
        if row:
            row.enabled = False
            self.session.commit()

    def save_schedule(self, channel_id: str, spec: ScheduleSpec, poll_duration_hours: int) -> Channel:
        row = self.get_by_channel_id(channel_id)
        if not row:
            raise ValueError(f"Channel not registered: {channel_id}")
        previous_spec = (
            ScheduleSpec.model_validate_json(row.schedule_json) if row.schedule_json else None
        )
        spec = _prepare_schedule_for_save(spec, previous_spec, row.tz or settings.default_timezone)
        row.schedule_json = spec.model_dump_json()
        row.poll_duration_hours = clamp_poll_duration_hours(poll_duration_hours)
        self.session.commit()
        self.session.refresh(row)
        return row

    def save_channel_settings(
        self,
        channel_id: str,
        *,
        team_id: str,
        spec: ScheduleSpec,
        poll_duration_hours: int,
        booking_url: str | None,
        poll_target_ids: list[str],
        calendar_invitees: list[CalendarInvitee],
        channel_member_ids: list[str],
        automatic_execution_enabled: bool,
    ) -> Channel:
        team_id = team_id.strip()
        row = self.get_by_channel_id(channel_id)
        if not row:
            if not team_id:
                raise ValueError(f"Team id required for new channel: {channel_id}")
            row = Channel(
                team_id=team_id,
                channel_id=channel_id,
                enabled=True,
                automatic_execution_enabled=automatic_execution_enabled,
            )
            self.session.add(row)
        elif team_id:
            row.team_id = team_id
        elif not row.team_id.strip():
            raise ValueError(f"Team id required for channel: {channel_id}")
        previous_spec = (
            ScheduleSpec.model_validate_json(row.schedule_json) if row.schedule_json else None
        )
        spec = _prepare_schedule_for_save(spec, previous_spec, row.tz or settings.default_timezone)
        row.automatic_execution_enabled = automatic_execution_enabled
        row.schedule_json = spec.model_dump_json()
        row.poll_duration_hours = clamp_poll_duration_hours(poll_duration_hours)
        row.booking_url_template = booking_url
        row.poll_target_ids_json = ids_to_json(poll_target_ids)
        row.calendar_invitees_json = invitees_to_json(calendar_invitees)
        row.channel_member_ids_json = ids_to_json(channel_member_ids)
        self.session.commit()
        self.session.refresh(row)
        return row

    def get_schedule(self, channel_id: str) -> ScheduleSpec | None:
        row = self.get_by_channel_id(channel_id)
        if not row or not row.schedule_json:
            return None
        return ScheduleSpec.model_validate_json(row.schedule_json)

    def save_participant_settings(
        self,
        channel_id: str,
        *,
        poll_target_ids: list[str],
        calendar_invitees: list[CalendarInvitee],
        channel_member_ids: list[str],
    ) -> Channel:
        row = self.get_by_channel_id(channel_id)
        if not row:
            raise ValueError(f"Channel not registered: {channel_id}")
        row.poll_target_ids_json = ids_to_json(poll_target_ids)
        row.calendar_invitees_json = invitees_to_json(calendar_invitees)
        row.channel_member_ids_json = ids_to_json(channel_member_ids)
        self.session.commit()
        self.session.refresh(row)
        return row


class WorkflowRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_open_run(self, channel_db_id: int) -> WorkflowRun | None:
        return self.session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.channel_id == channel_db_id)
            .where(WorkflowRun.state.in_(_POLL_OPEN_STATES))
            .order_by(WorkflowRun.id.desc())
        )

    def get_active_run(self, channel_db_id: int) -> WorkflowRun | None:
        return self.session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.channel_id == channel_db_id)
            .where(WorkflowRun.state.in_(_ACTIVE_STATES))
            .order_by(WorkflowRun.id.desc())
        )

    def list_open_runs(self, channel_db_id: int) -> list[WorkflowRun]:
        return list(
            self.session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.channel_id == channel_db_id)
                .where(WorkflowRun.state.in_(_POLL_OPEN_STATES))
                .order_by(WorkflowRun.id.desc())
            ).all()
        )

    def get_run(self, run_id: int) -> WorkflowRun | None:
        return self.session.get(WorkflowRun, run_id)

    def create_run(
        self,
        channel_db_id: int,
        *,
        state: str = "POLL_OPEN",
        scheduled_for: datetime | None = None,
        poll_deadline: datetime | None = None,
        thread_ts: str | None = None,
        target_member_ids_json: str | None = None,
        poll_semantics: str | None = "unavailable",
    ) -> WorkflowRun:
        run = WorkflowRun(
            channel_id=channel_db_id,
            state=state,
            scheduled_for=scheduled_for,
            poll_deadline=poll_deadline,
            thread_ts=thread_ts,
            target_member_ids_json=target_member_ids_json,
            poll_semantics=poll_semantics,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def update_run(self, run: WorkflowRun, **fields) -> WorkflowRun:
        for k, v in fields.items():
            setattr(run, k, v)
        self.session.commit()
        self.session.refresh(run)
        return run

    def toggle_vote(self, run_id: int, slack_user_id: str, date_iso: str) -> bool:
        """Toggle vote; returns True if vote added, False if removed."""
        existing = self.session.scalar(
            select(PollVote).where(
                PollVote.run_id == run_id,
                PollVote.slack_user_id == slack_user_id,
                PollVote.date_iso == date_iso,
            )
        )
        if existing:
            self.session.delete(existing)
            self.session.commit()
            return False
        self.session.add(PollVote(run_id=run_id, slack_user_id=slack_user_id, date_iso=date_iso))
        self.session.commit()
        return True

    def votes_by_user(self, run_id: int) -> dict[str, set[str]]:
        rows = self.session.scalars(select(PollVote).where(PollVote.run_id == run_id)).all()
        out: dict[str, set[str]] = {}
        for row in rows:
            out.setdefault(row.slack_user_id, set()).add(row.date_iso)
        return out

    def clear_votes(self, run_id: int) -> None:
        self.session.execute(delete(PollVote).where(PollVote.run_id == run_id))
        self.session.commit()

    def record_assignee(self, channel_db_id: int, user_id: str) -> None:
        self.session.add(AssigneeHistory(channel_id=channel_db_id, user_id=user_id))
        self.session.commit()

    def last_assignee(self, channel_db_id: int) -> str | None:
        row = self.session.scalar(
            select(AssigneeHistory)
            .where(AssigneeHistory.channel_id == channel_db_id)
            .order_by(AssigneeHistory.run_at.desc())
        )
        return row.user_id if row else None


def _prepare_schedule_for_save(
    spec: ScheduleSpec,
    previous_spec: ScheduleSpec | None,
    tz_name: str,
) -> ScheduleSpec:
    if _same_monthly_cadence(spec, previous_spec) and _has_month_anchor(previous_spec):
        return spec.model_copy(
            update={
                "month_anchor_year": previous_spec.month_anchor_year,
                "month_anchor_month": previous_spec.month_anchor_month,
            }
        )
    anchor = datetime.now(ZoneInfo(tz_name))
    return spec.with_month_anchor(anchor, tz_name)


def _same_monthly_cadence(spec: ScheduleSpec, previous_spec: ScheduleSpec | None) -> bool:
    if previous_spec is None:
        return False
    return (
        spec.type == previous_spec.type
        and spec.weekday == previous_spec.weekday
        and spec.day == previous_spec.day
        and spec.nth == previous_spec.nth
        and spec.month_interval == previous_spec.month_interval
    )


def _has_month_anchor(spec: ScheduleSpec | None) -> bool:
    return bool(
        spec
        and spec.month_anchor_year is not None
        and spec.month_anchor_month is not None
    )
