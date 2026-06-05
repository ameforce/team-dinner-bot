# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.config import settings
from app.db.models import Channel
from app.db.repository import ChannelRepository, WorkflowRepository
from app.integrations.calendar_links import (
    DinnerCalendarEvent,
    build_google_calendar_event_payload,
    build_google_calendar_url,
)
from app.integrations.google_calendar import GoogleCalendarClient, GoogleCalendarConfig
from app.integrations.slack_users import (
    collect_attendee_emails,
    list_human_member_ids,
    list_human_members,
)
from app.settings_defaults import clamp_poll_duration_hours
from app.workflow.dates import business_days_rest_of_month
from app.workflow.poll import (
    choose_dinner_date_with_pool,
    format_tally_message,
    poll_blocks,
    tally_votes_with_pool,
    winning_option_json,
)
from app.workflow.participants import (
    CalendarInvitee,
    ids_from_json,
    invitees_from_json,
    resolve_calendar_invitees,
    resolve_poll_target_ids,
)
from app.workflow.states import WorkflowState

logger = logging.getLogger(__name__)
SELECTION_AUDIT_SCHEMA_VERSION = 1


def _candidate_date_isos(run, ch: Channel) -> set[str]:
    return set(_candidate_date_iso_list(run, ch))


def _candidate_date_iso_list(run, ch: Channel) -> list[str]:
    anchor = run.scheduled_for or run.created_at or datetime.now(ZoneInfo(ch.tz))
    return [d.isoformat() for d in business_days_rest_of_month(after=anchor, tz_name=ch.tz)]


def _candidate_dates(run, ch: Channel) -> list[date]:
    anchor = run.scheduled_for or run.created_at or datetime.now(ZoneInfo(ch.tz))
    return business_days_rest_of_month(after=anchor, tz_name=ch.tz)


def _filter_votes_to_candidates(
    votes_by_user: dict[str, set[str]], valid_dates: set[str]
) -> dict[str, set[str]]:
    return {
        user_id: valid
        for user_id, dates in votes_by_user.items()
        if (valid := {date_iso for date_iso in dates if date_iso in valid_dates})
    }


class WorkflowEngine:
    def __init__(self, session_factory: sessionmaker, slack_client, *, calendar_client=None):
        self.session_factory = session_factory
        self.client = slack_client
        self.calendar_client = calendar_client or GoogleCalendarClient(
            GoogleCalendarConfig(
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
                refresh_token=settings.google_refresh_token,
                calendar_id=settings.google_calendar_id,
            )
        )
        self._schedule_poll_close: Callable[[int, datetime], Any] | None = None
        self._cancel_poll_close: Callable[[int], Any] | None = None

    def bind_poll_scheduler(
        self,
        schedule_poll_close: Callable[[int, datetime], Any],
        cancel_poll_close: Callable[[int], Any],
    ) -> None:
        self._schedule_poll_close = schedule_poll_close
        self._cancel_poll_close = cancel_poll_close

    @staticmethod
    def _normalize_deadline(deadline: datetime, tz: ZoneInfo) -> datetime:
        if deadline.tzinfo is None:
            return deadline.replace(tzinfo=tz)
        return deadline.astimezone(tz)

    def _abort_open_runs(self, channel_db_id: int) -> list[int]:
        """Silently end stale POLL_OPEN runs (no Slack posts)."""
        aborted: list[int] = []
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            for run in wf.list_open_runs(channel_db_id):
                wf.update_run(run, state=WorkflowState.DONE)
                aborted.append(run.id)
        for run_id in aborted:
            if self._cancel_poll_close:
                self._cancel_poll_close(run_id)
        return aborted

    def start_channel_run(self, slack_channel_id: str, *, replace: bool = False) -> str | None:
        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            if not ch or not ch.enabled:
                return m.MSG_CHANNEL_DISABLED
            if not ch.schedule_json:
                return m.MSG_NO_SCHEDULE
            wf = WorkflowRepository(session)
            if wf.get_open_run(ch.id):
                if not replace:
                    return m.MSG_POLL_ALREADY_OPEN
                self._abort_open_runs(ch.id)
            tz = ZoneInfo(ch.tz)
            now = datetime.now(tz)
            candidates = business_days_rest_of_month(after=now, tz_name=ch.tz)
            if not candidates:
                return m.MSG_NO_POLL_DATES
            configured_poll_target_ids = ids_from_json(ch.poll_target_ids_json)
            has_configured_poll_targets = ch.poll_target_ids_json is not None
            known_member_ids = ids_from_json(ch.channel_member_ids_json)
            poll_hours = clamp_poll_duration_hours(ch.poll_duration_hours)
            deadline = self._normalize_deadline(
                now + timedelta(hours=poll_hours),
                tz,
            )
            run = wf.create_run(
                ch.id,
                state=WorkflowState.POLL_OPEN,
                scheduled_for=now,
                poll_deadline=deadline,
            )

        members = list_human_members(self.client, slack_channel_id)
        current_member_ids = [member.user_id for member in members]
        if has_configured_poll_targets or known_member_ids:
            target_member_ids = resolve_poll_target_ids(
                configured_target_ids=configured_poll_target_ids,
                known_member_ids=known_member_ids,
                current_member_ids=current_member_ids,
            )
        else:
            target_member_ids = current_member_ids
        target_member_ids_json = json.dumps(
            target_member_ids,
            ensure_ascii=False,
        )
        with self.session_factory() as session:
            run = WorkflowRepository(session).get_run(run.id)
            if run:
                WorkflowRepository(session).update_run(
                    run,
                    target_member_ids_json=target_member_ids_json,
                )
        blocks = poll_blocks(
            run.id,
            candidates,
            deadline,
            target_user_ids=target_member_ids,
            unavailable_by_user={},
        )
        resp = self.client.chat_postMessage(
            channel=slack_channel_id,
            text=m.MSG_POLL_STARTED,
            blocks=blocks,
        )
        thread_ts = resp.get("ts")
        with self.session_factory() as session:
            run = WorkflowRepository(session).get_run(run.id)
            if run:
                WorkflowRepository(session).update_run(run, thread_ts=thread_ts)
        if self._schedule_poll_close:
            self._schedule_poll_close(run.id, deadline)
        return None

    def cancel_current_run(self, slack_channel_id: str) -> str:
        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            if not ch or not ch.enabled:
                return m.MSG_CHANNEL_DISABLED
            wf = WorkflowRepository(session)
            run = wf.get_active_run(ch.id)
            if not run:
                return m.MSG_NO_ACTIVE_RUN
            run_id = run.id
            thread_ts = run.thread_ts
            wf.update_run(run, state=WorkflowState.DONE)
        if self._cancel_poll_close:
            self._cancel_poll_close(run_id)
        self.client.chat_postMessage(
            channel=slack_channel_id,
            thread_ts=thread_ts,
            text=m.MSG_RUN_CANCELLED,
        )
        return m.MSG_RUN_CANCELLED

    def on_poll_vote(self, run_id: int, user_id: str, date_iso: str, channel_id: str) -> str:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run or run.state != WorkflowState.POLL_OPEN:
                return m.MSG_POLL_CLOSED
            ch = session.get(Channel, run.channel_id)
            if not ch or date_iso not in _candidate_date_isos(run, ch):
                return m.MSG_INVALID_POLL_OPTION
            added = wf.toggle_vote(run_id, user_id, date_iso)
            votes = wf.votes_by_user(run_id)
            candidates = _candidate_dates(run, ch)
            target_member_ids = _target_member_ids_for_run(run)
            thread_ts = run.thread_ts
        if thread_ts and hasattr(self.client, "chat_update"):
            self.client.chat_update(
                channel=channel_id,
                ts=thread_ts,
                text=m.MSG_POLL_STARTED,
                blocks=poll_blocks(
                    run_id,
                    candidates,
                    self._normalize_deadline(run.poll_deadline or datetime.now(), ZoneInfo(ch.tz)),
                    target_user_ids=target_member_ids,
                    unavailable_by_user=votes,
                ),
            )
        if added:
            return m.MSG_POLL_VOTE_ADDED.format(date=date_iso)
        return m.MSG_POLL_VOTE_REMOVED.format(date=date_iso)

    def close_poll(self, run_id: int) -> None:
        if self._cancel_poll_close:
            self._cancel_poll_close(run_id)
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run or run.state != WorkflowState.POLL_OPEN:
                return
            ch = session.get(Channel, run.channel_id)
            if not ch:
                return
            candidate_isos = _candidate_date_iso_list(run, ch)
            votes = _filter_votes_to_candidates(wf.votes_by_user(run_id), set(candidate_isos))
            if run.poll_semantics == "unavailable":
                winner, counts, date_selection_pool = choose_dinner_date_with_pool(
                    votes,
                    candidate_isos,
                    choose=random.choice,
                )
            else:
                winner, counts, date_selection_pool = tally_votes_with_pool(votes)
            if not winner:
                self.client.chat_postMessage(
                    channel=ch.channel_id,
                    thread_ts=run.thread_ts,
                    text=m.MSG_NO_VOTES_SKIP,
                )
                wf.update_run(run, state=WorkflowState.DONE)
                return
            wf.update_run(
                run,
                state=WorkflowState.POLL_CLOSED,
                winning_option_json=winning_option_json(winner, counts),
                selection_audit_json=_merge_selection_audit(
                    run.selection_audit_json,
                    date=_date_audit_payload(
                        candidate_pool=candidate_isos,
                        selection_pool=date_selection_pool,
                        selected=winner,
                        counts=counts,
                        poll_semantics=run.poll_semantics,
                    ),
                ),
            )
            logger.info(
                "date_selection_audit run_id=%s channel_id=%s selected=%s",
                run.id,
                ch.channel_id,
                winner,
            )
            slack_channel_id = ch.channel_id
            thread_ts = run.thread_ts
            poll_closed_run = run

        self.client.chat_postMessage(
            channel=slack_channel_id,
            thread_ts=thread_ts,
            text=format_tally_message(winner, counts),
        )
        self._assign_booking(poll_closed_run.id, slack_channel_id, thread_ts)

    def _assign_booking(self, run_id: int, slack_channel_id: str, thread_ts: str | None) -> None:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run:
                return
            if run.state in (WorkflowState.DONE, WorkflowState.BOOKING_ASSIGNED):
                return
            ch = session.get(Channel, run.channel_id)
            if not ch:
                return
            target_member_ids = _target_member_ids_for_run(run)
            if target_member_ids:
                members = target_member_ids
                member_source = "target_member_snapshot"
            else:
                members = list_human_member_ids(self.client, slack_channel_id)
                member_source = "current_channel_members"
            current_member_ids = list_human_member_ids(self.client, slack_channel_id)
            calendar_invitees = _calendar_invitees_for_channel(ch, current_member_ids)
            required_slack_ids = [
                invitee.value
                for invitee in calendar_invitees
                if invitee.kind == "slack" and invitee.role == "required"
            ]
            optional_slack_ids = [
                invitee.value
                for invitee in calendar_invitees
                if invitee.kind == "slack" and invitee.role == "optional"
            ]
            required_external_emails = [
                invitee.value
                for invitee in calendar_invitees
                if invitee.kind == "email" and invitee.role == "required"
            ]
            optional_external_emails = [
                invitee.value
                for invitee in calendar_invitees
                if invitee.kind == "email" and invitee.role == "optional"
            ]
            emails, missing_member_ids = collect_attendee_emails(
                session, self.client, required_slack_ids
            )
            optional_emails, optional_missing_member_ids = collect_attendee_emails(
                session, self.client, optional_slack_ids
            )
            emails.extend(required_external_emails)
            optional_emails.extend(optional_external_emails)
            missing_member_ids.extend(optional_missing_member_ids)
            last = wf.last_assignee(ch.id)
            pool = [u for u in members if u != last] or members
            if not pool:
                self.client.chat_postMessage(
                    channel=slack_channel_id,
                    thread_ts=thread_ts,
                    text=m.MSG_NO_ASSIGNEE,
                )
                return
            assignee = random.choice(pool)
            wf.update_run(
                run,
                state=WorkflowState.BOOKING_ASSIGNED,
                assignee_user_id=assignee,
                selection_audit_json=_merge_selection_audit(
                    run.selection_audit_json,
                    assignee=_assignee_audit_payload(
                        member_source=member_source,
                        target_member_ids=members,
                        previous_assignee=last,
                        candidate_pool=pool,
                        selected=assignee,
                    ),
                ),
            )
            logger.info(
                "assignee_selection_audit run_id=%s channel_id=%s selected=%s",
                run.id,
                slack_channel_id,
                assignee,
            )
            wf.record_assignee(ch.id, assignee)
            booking_url = ch.booking_url_template or m.MSG_BOOKING_URL_MISSING
            winner = {}
            if run.winning_option_json:
                winner = json.loads(run.winning_option_json)
            tz_name = ch.tz

        date_label = winner.get("date", "?")
        calendar_url = None
        calendar_create_error = None
        try:
            event = DinnerCalendarEvent(
                title=f"{m.BOT_NAME} 회식",
                dinner_date=date.fromisoformat(date_label),
                tz_name=tz_name,
                booking_url=None if booking_url == m.MSG_BOOKING_URL_MISSING else booking_url,
                attendee_emails=emails,
                optional_attendee_emails=optional_emails,
                missing_member_ids=missing_member_ids,
            )
            create_result = self.calendar_client.create_event(
                build_google_calendar_event_payload(event)
            )
            if create_result.ok and create_result.html_link:
                calendar_url = create_result.html_link
            else:
                calendar_create_error = create_result.error
                calendar_url = build_google_calendar_url(event)
        except ValueError:
            logger.exception("Invalid winning date for calendar link: %s", date_label)
        calendar_line = (
            f"Google 캘린더 등록: {calendar_url}\n"
            if calendar_url
            else "Google 캘린더 등록: 확정 날짜를 확인할 수 없어 링크를 만들지 못했습니다.\n"
        )
        if calendar_create_error:
            calendar_line = f"Google 캘린더 직접 생성 실패: {calendar_create_error}\n{calendar_line}"
        dm_text = (
            f"{m.MSG_BOOKING_DM_TITLE}\n"
            f"\ud655\uc815 \ud6c4\ubcf4\uc77c: `{date_label}`\n"
            f"\uc608\uc57d \ub9c1\ud06c: {booking_url}\n"
            f"{calendar_line}"
            "\uc608\uc57d \uc644\ub8cc \ud6c4 \uc544\ub798 \ubc84\ud2bc\uc744 \ub20c\ub7ec \uc8fc\uc138\uc694."
        )
        try:
            self.client.chat_postMessage(
                channel=assignee,
                text=dm_text,
                blocks=[
                    {"type": "section", "text": {"type": "mrkdwn", "text": dm_text}},
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": m.MSG_BOOKING_DONE_BTN},
                                "style": "primary",
                                "action_id": "booking_done",
                                "value": str(run_id),
                            }
                        ],
                    },
                ],
            )
        except Exception:
            logger.exception("DM to assignee failed")
        self.client.chat_postMessage(
            channel=slack_channel_id,
            thread_ts=thread_ts,
            text=f"<@{assignee}> \ub2d8\uc774 \uc774\ubc88 \ud68c\uc2dd \uc608\uc57d \ub2f4\ub2f9\uc785\ub2c8\ub2e4.",
        )

    def on_booking_done(
        self, run_id: int, user_id: str, *, announce_channel: bool = True
    ) -> str:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run:
                return m.MSG_WORKFLOW_NOT_FOUND
            if run.state == WorkflowState.DONE:
                return m.MSG_ALREADY_DONE
            if run.state != WorkflowState.BOOKING_ASSIGNED:
                return m.MSG_BOOKING_NOT_READY
            if run.assignee_user_id and run.assignee_user_id != user_id:
                return m.MSG_ONLY_ASSIGNEE
            ch = session.get(Channel, run.channel_id)
            wf.update_run(run, state=WorkflowState.DONE)
            channel_id = ch.channel_id if ch else ""
            thread_ts = run.thread_ts
        if self._cancel_poll_close:
            self._cancel_poll_close(run_id)
        if announce_channel and channel_id:
            self.client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=(
                    f"<@{user_id}> \ub2d8\uc774 \uc608\uc57d\uc744 \uc644\ub8cc\ud588\uc2b5\ub2c8\ub2e4. "
                    "\uc774\ubc88 \ud68c\uc2dd \uc77c\uc815 \ud750\ub984\uc774 \uc885\ub8cc\ub418\uc5c8\uc2b5\ub2c8\ub2e4."
                ),
            )
        return m.MSG_BOOKING_DONE_OK


def _date_audit_payload(
    *,
    candidate_pool: list[str],
    selection_pool: list[str],
    selected: str,
    counts: dict[str, int],
    poll_semantics: str | None,
) -> dict[str, Any]:
    return {
        "candidate_pool": candidate_pool,
        "selection_pool": selection_pool,
        "selected": selected,
        "counts": counts,
        "poll_semantics": poll_semantics,
    }


def _assignee_audit_payload(
    *,
    member_source: str,
    target_member_ids: list[str],
    previous_assignee: str | None,
    candidate_pool: list[str],
    selected: str,
) -> dict[str, Any]:
    return {
        "member_source": member_source,
        "target_member_ids": target_member_ids,
        "previous_assignee": previous_assignee,
        "candidate_pool": candidate_pool,
        "selected": selected,
    }


def _merge_selection_audit(existing_json: str | None, **sections: dict[str, Any]) -> str:
    if existing_json:
        try:
            audit = json.loads(existing_json)
        except json.JSONDecodeError:
            logger.exception("Invalid selection_audit_json; replacing audit payload")
            audit = {}
    else:
        audit = {"schema_version": SELECTION_AUDIT_SCHEMA_VERSION}
    audit["schema_version"] = SELECTION_AUDIT_SCHEMA_VERSION
    for key, value in sections.items():
        audit[key] = value
    return json.dumps(audit, ensure_ascii=False, sort_keys=True)


def _target_member_ids_for_run(run) -> list[str]:
    if not run.target_member_ids_json:
        return []
    try:
        data = json.loads(run.target_member_ids_json)
    except json.JSONDecodeError:
        logger.exception("Invalid target_member_ids_json for run_id=%s", run.id)
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if isinstance(item, str) and item]


def _calendar_invitees_for_channel(ch: Channel, current_member_ids: list[str]) -> list[CalendarInvitee]:
    configured = invitees_from_json(ch.calendar_invitees_json)
    known_member_ids = ids_from_json(ch.channel_member_ids_json)
    if configured or known_member_ids:
        return resolve_calendar_invitees(
            configured_invitees=configured,
            known_member_ids=known_member_ids,
            current_member_ids=current_member_ids,
        )
    return [
        CalendarInvitee(value=user_id, role="required", kind="slack")
        for user_id in current_member_ids
    ]
