# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import random
from hashlib import sha256
from collections.abc import Callable
from dataclasses import dataclass
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
from app.rendering import (
    MessageAudience,
    RenderedSlackMessage,
    ResultCode,
    post_rendered,
    render_assignee_dm,
    render_assignee_public,
    render_assignee_unavailable,
    render_booking_done,
    render_poll_open,
    render_poll_result,
    update_rendered,
)
from app.integrations.slack_users import (
    collect_attendee_emails,
    list_human_member_ids,
    list_human_members,
)
from app.settings_defaults import clamp_poll_duration_hours
from app.workflow.dates import business_days_rest_of_month
from app.workflow.poll import (
    choose_dinner_date_with_pool,
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
from app.workflow.states import (
    CalendarOutcome,
    OutboundEffectStatus,
    OutboundEffectType,
    WorkflowState,
)

logger = logging.getLogger(__name__)
SELECTION_AUDIT_SCHEMA_VERSION = 1
MAX_ASSIGNEE_DM_ATTEMPTS = 3


@dataclass(frozen=True)
class PollVoteResult:
    needs_feedback: bool
    feedback_text: str | None = None
    added: bool | None = None

    @classmethod
    def success(cls, *, added: bool) -> "PollVoteResult":
        return cls(needs_feedback=False, added=added)

    @classmethod
    def feedback(cls, text: str) -> "PollVoteResult":
        return cls(needs_feedback=True, feedback_text=text)


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

    def _abort_active_run(self, channel_db_id: int) -> int | None:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_active_run(channel_db_id)
            if not run:
                return None
            run_id = run.id
            wf.update_run(
                run,
                state=WorkflowState.DONE,
                terminal_reason="REPLACED",
            )
        if self._cancel_poll_close:
            self._cancel_poll_close(run_id)
        return run_id

    @staticmethod
    def _is_ambiguous_failure(exc: Exception) -> bool:
        return isinstance(exc, (TimeoutError, ConnectionError))

    def _post_effect(
        self,
        *,
        run_id: int,
        effect_type: OutboundEffectType,
        channel: str,
        message: RenderedSlackMessage,
        thread_ts: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            effect = wf.get_effect("workflow_run", str(run_id), effect_type)
            if not effect:
                effect = wf.ensure_effect(
                    aggregate_type="workflow_run",
                    aggregate_id=str(run_id),
                    effect_type=effect_type,
                    idempotency_key=(
                        f"run:{run_id}:{effect_type.value.lower().replace('_', '-')}:v1"
                    ),
                )
            if effect.status == OutboundEffectStatus.SENT:
                return True
            if effect.status == OutboundEffectStatus.UNKNOWN:
                return False
            if (
                effect.status == OutboundEffectStatus.PENDING
                and effect.attempt_count > 0
            ):
                wf.update_effect(
                    effect,
                    status=OutboundEffectStatus.UNKNOWN,
                    error_code="DELIVERY_UNRESOLVED",
                )
                return False
            wf.update_effect(
                effect,
                status=OutboundEffectStatus.PENDING,
                increment_attempt=True,
            )
        kwargs: dict[str, Any] = {}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        if metadata:
            kwargs["metadata"] = metadata
        try:
            response = post_rendered(self.client, channel, message, **kwargs)
        except Exception as exc:
            status = (
                OutboundEffectStatus.UNKNOWN
                if self._is_ambiguous_failure(exc)
                else OutboundEffectStatus.FAILED
            )
            with self.session_factory() as session:
                wf = WorkflowRepository(session)
                effect = wf.get_effect("workflow_run", str(run_id), effect_type)
                if effect:
                    wf.update_effect(
                        effect,
                        status=status,
                        error_code=type(exc).__name__,
                    )
            logger.exception("outbound effect failed run_id=%s type=%s", run_id, effect_type)
            return False
        remote_ref = response.get("ts") if isinstance(response, dict) else None
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            effect = wf.get_effect("workflow_run", str(run_id), effect_type)
            if effect:
                wf.update_effect(
                    effect,
                    status=OutboundEffectStatus.SENT,
                    remote_ref=remote_ref,
                )
        return True

    def _post_open_poll(self, run_id: int, slack_channel_id: str) -> bool:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run or run.state != WorkflowState.POLL_STARTING:
                return bool(run and run.state == WorkflowState.POLL_OPEN)
            ch = session.get(Channel, run.channel_id)
            if not ch:
                return False
            candidates = _candidate_dates(run, ch)
            deadline = self._normalize_deadline(
                run.poll_deadline or datetime.now(ZoneInfo(ch.tz)), ZoneInfo(ch.tz)
            )
            target_member_ids = _target_member_ids_for_run(run)
            effect = wf.get_effect(
                "workflow_run", str(run.id), OutboundEffectType.POLL_OPEN_MESSAGE
            )
            if not effect:
                return False
            if effect.status == OutboundEffectStatus.UNKNOWN:
                wf.update_run(
                    run,
                    state=WorkflowState.NEEDS_ATTENTION,
                    attention_reason="POLL_POST_UNKNOWN",
                    result_code="POLL_POST_UNKNOWN",
                )
                return False
            if (
                effect.status == OutboundEffectStatus.PENDING
                and effect.attempt_count > 0
            ):
                wf.update_run(
                    run,
                    state=WorkflowState.NEEDS_ATTENTION,
                    attention_reason="POLL_POST_UNRESOLVED",
                    result_code="POLL_POST_UNRESOLVED",
                )
                return False
            if effect.status == OutboundEffectStatus.SENT and effect.remote_ref:
                wf.update_run(
                    run,
                    state=WorkflowState.POLL_OPEN,
                    thread_ts=effect.remote_ref,
                )
                return True
            wf.update_effect(
                effect,
                status=OutboundEffectStatus.PENDING,
                increment_attempt=True,
            )
        message = render_poll_open(
            blocks=poll_blocks(
                run_id,
                candidates,
                deadline,
                target_user_ids=target_member_ids,
                unavailable_by_user={},
            )
        )
        try:
            response = post_rendered(
                self.client,
                slack_channel_id,
                message,
                metadata={
                    "event_type": "team_dinner_poll",
                    "event_payload": {"run_id": str(run_id)},
                },
            )
        except Exception as exc:
            ambiguous = self._is_ambiguous_failure(exc)
            with self.session_factory() as session:
                wf = WorkflowRepository(session)
                run = wf.get_run(run_id)
                effect = wf.get_effect(
                    "workflow_run", str(run_id), OutboundEffectType.POLL_OPEN_MESSAGE
                )
                if run and effect:
                    wf.transition_with_effect(
                        run,
                        effect,
                        effect_status=(
                            OutboundEffectStatus.UNKNOWN
                            if ambiguous
                            else OutboundEffectStatus.FAILED
                        ),
                        error_code=type(exc).__name__,
                        state=(
                            WorkflowState.NEEDS_ATTENTION
                            if ambiguous
                            else WorkflowState.FAILED
                        ),
                        attention_reason="POLL_POST_UNKNOWN" if ambiguous else None,
                        terminal_reason=None if ambiguous else "POLL_POST_FAILED",
                        result_code="POLL_POST_UNKNOWN" if ambiguous else "POLL_POST_FAILED",
                    )
            logger.exception("poll post failed run_id=%s", run_id)
            return False
        thread_ts = response.get("ts") if isinstance(response, dict) else None
        if not thread_ts:
            with self.session_factory() as session:
                wf = WorkflowRepository(session)
                run = wf.get_run(run_id)
                effect = wf.get_effect(
                    "workflow_run", str(run_id), OutboundEffectType.POLL_OPEN_MESSAGE
                )
                if run and effect:
                    wf.transition_with_effect(
                        run,
                        effect,
                        effect_status=OutboundEffectStatus.UNKNOWN,
                        error_code="SLACK_TS_MISSING",
                        state=WorkflowState.NEEDS_ATTENTION,
                        attention_reason="POLL_POST_UNKNOWN",
                        result_code="POLL_POST_UNKNOWN",
                    )
            return False
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            effect = wf.get_effect(
                "workflow_run", str(run_id), OutboundEffectType.POLL_OPEN_MESSAGE
            )
            if not run or not effect:
                return False
            wf.transition_with_effect(
                run,
                effect,
                effect_status=OutboundEffectStatus.SENT,
                remote_ref=thread_ts,
                state=WorkflowState.POLL_OPEN,
                thread_ts=thread_ts,
            )
        if self._schedule_poll_close:
            self._schedule_poll_close(run_id, deadline)
        return True

    def start_channel_run(self, slack_channel_id: str, *, replace: bool = False) -> str | None:
        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            if not ch or not ch.enabled:
                return m.MSG_CHANNEL_DISABLED
            if not ch.schedule_json:
                return m.MSG_NO_SCHEDULE
            wf = WorkflowRepository(session)
            if wf.get_active_run(ch.id):
                if not replace:
                    return m.MSG_POLL_ALREADY_OPEN
                self._abort_active_run(ch.id)
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
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            if not ch or not ch.enabled:
                return m.MSG_CHANNEL_DISABLED
            run = WorkflowRepository(session).create_claimed_run(
                ch.id,
                state=WorkflowState.POLL_STARTING,
                scheduled_for=now,
                poll_deadline=deadline,
                target_member_ids_json=target_member_ids_json,
                poll_semantics="unavailable",
                initial_effect_type=OutboundEffectType.POLL_OPEN_MESSAGE,
            )
        if not run:
            return m.MSG_POLL_ALREADY_OPEN
        if self._post_open_poll(run.id, slack_channel_id):
            return None
        return m.MSG_POLL_START_FAILED

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
            terminal_reason = (
                "CANCELLED_NO_ASSIGNEE"
                if run.attention_reason == "NO_ASSIGNEE_AVAILABLE"
                else "CANCELLED"
            )
            wf.update_run(
                run,
                state=WorkflowState.DONE,
                terminal_reason=terminal_reason,
            )
        if self._cancel_poll_close:
            self._cancel_poll_close(run_id)
        post_rendered(
            self.client,
            slack_channel_id,
            RenderedSlackMessage(
                ResultCode.OPERATION_FAILED,
                MessageAudience.PUBLIC,
                m.MSG_RUN_CANCELLED,
            ),
            thread_ts=thread_ts,
        )
        return m.MSG_RUN_CANCELLED

    def on_poll_vote(self, run_id: int, user_id: str, date_iso: str, channel_id: str) -> PollVoteResult:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run or run.state != WorkflowState.POLL_OPEN:
                return PollVoteResult.feedback(m.MSG_POLL_CLOSED)
            ch = session.get(Channel, run.channel_id)
            if not ch or date_iso not in _candidate_date_isos(run, ch):
                return PollVoteResult.feedback(m.MSG_INVALID_POLL_OPTION)
            added = wf.toggle_vote(run_id, user_id, date_iso)
            votes = wf.votes_by_user(run_id)
            candidates = _candidate_dates(run, ch)
            target_member_ids = _target_member_ids_for_run(run)
            thread_ts = run.thread_ts
        if thread_ts and hasattr(self.client, "chat_update"):
            update_rendered(
                self.client,
                channel_id,
                thread_ts,
                render_poll_open(
                    blocks=poll_blocks(
                        run_id,
                        candidates,
                        self._normalize_deadline(
                            run.poll_deadline or datetime.now(), ZoneInfo(ch.tz)
                        ),
                        target_user_ids=target_member_ids,
                        unavailable_by_user=votes,
                    )
                ),
            )
        return PollVoteResult.success(added=added)

    def close_poll(self, run_id: int) -> None:
        if self._cancel_poll_close:
            self._cancel_poll_close(run_id)
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run:
                return
            if run.state == WorkflowState.CLOSE_COMPUTED:
                ch = session.get(Channel, run.channel_id)
                if not ch:
                    return
                slack_channel_id = ch.channel_id
                thread_ts = run.thread_ts
            elif run.state != WorkflowState.POLL_OPEN:
                return
            else:
                ch = session.get(Channel, run.channel_id)
                if not ch:
                    return
                candidate_isos = _candidate_date_iso_list(run, ch)
                votes = _filter_votes_to_candidates(
                    wf.votes_by_user(run_id), set(candidate_isos)
                )
                if run.poll_semantics == "unavailable":
                    winner, counts, date_selection_pool = choose_dinner_date_with_pool(
                        votes,
                        candidate_isos,
                        choose=random.choice,
                    )
                else:
                    winner, counts, date_selection_pool = tally_votes_with_pool(votes)
                if not winner:
                    wf.update_run(
                        run,
                        state=WorkflowState.DONE,
                        terminal_reason="NO_VOTES",
                    )
                    post_rendered(
                        self.client,
                        ch.channel_id,
                        RenderedSlackMessage(
                            ResultCode.OPERATION_FAILED,
                            MessageAudience.PUBLIC,
                            m.MSG_NO_VOTES_SKIP,
                        ),
                        thread_ts=run.thread_ts,
                    )
                    return
                wf.update_run(
                    run,
                    state=WorkflowState.CLOSE_COMPUTED,
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
                wf.ensure_effect(
                    aggregate_type="workflow_run",
                    aggregate_id=str(run.id),
                    effect_type=OutboundEffectType.POLL_RESULT_NOTICE,
                    idempotency_key=f"run:{run.id}:poll-result:v1",
                )
                logger.info(
                    "date_selection_audit run_id=%s channel_id=%s selected=%s",
                    run.id,
                    ch.channel_id,
                    winner,
                )
                slack_channel_id = ch.channel_id
                thread_ts = run.thread_ts
        self._deliver_poll_result(run_id, slack_channel_id, thread_ts)

    def _deliver_poll_result(
        self, run_id: int, slack_channel_id: str, thread_ts: str | None
    ) -> None:
        with self.session_factory() as session:
            run = WorkflowRepository(session).get_run(run_id)
            if not run or not run.winning_option_json:
                return
            winner_payload = json.loads(run.winning_option_json)
            winner = winner_payload["date"]
            counts = winner_payload.get("counts", {})
        delivered = self._post_effect(
            run_id=run_id,
            effect_type=OutboundEffectType.POLL_RESULT_NOTICE,
            channel=slack_channel_id,
            thread_ts=thread_ts,
            message=render_poll_result(winner, counts),
            metadata={
                "event_type": "team_dinner_poll_result",
                "event_payload": {"run_id": str(run_id)},
            },
        )
        if delivered:
            self._assign_booking(run_id, slack_channel_id, thread_ts)
            return
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            effect = wf.get_effect(
                "workflow_run", str(run_id), OutboundEffectType.POLL_RESULT_NOTICE
            )
            if run and effect and effect.status == OutboundEffectStatus.UNKNOWN:
                wf.update_run(
                    run,
                    state=WorkflowState.NEEDS_ATTENTION,
                    attention_reason="POLL_RESULT_UNKNOWN",
                    result_code="POLL_RESULT_UNKNOWN",
                )

    def _assign_booking(self, run_id: int, slack_channel_id: str, thread_ts: str | None) -> None:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run or run.state in (WorkflowState.DONE, WorkflowState.FAILED):
                return
            ch = session.get(Channel, run.channel_id)
            if not ch:
                return
            if run.state == WorkflowState.NEEDS_ATTENTION:
                if run.attention_reason != "NO_ASSIGNEE_AVAILABLE":
                    return
                configured = ids_from_json(ch.poll_target_ids_json)
                members = configured or list_human_member_ids(
                    self.client, slack_channel_id
                )
                if not members:
                    return
                run.target_member_ids_json = json.dumps(members, ensure_ascii=False)
                run.state = WorkflowState.CLOSE_COMPUTED
                run.attention_reason = None
                run.result_code = None
                session.commit()
            if run.state == WorkflowState.CLOSE_COMPUTED:
                target_member_ids = _target_member_ids_for_run(run)
                if target_member_ids:
                    members = target_member_ids
                    member_source = "target_member_snapshot"
                else:
                    members = list_human_member_ids(self.client, slack_channel_id)
                    member_source = "current_channel_members"
                last = wf.last_assignee(ch.id)
                pool = [user for user in members if user != last] or members
                if not pool:
                    wf.update_run(
                        run,
                        state=WorkflowState.NEEDS_ATTENTION,
                        attention_reason="NO_ASSIGNEE_AVAILABLE",
                        result_code="ASSIGNEE_UNAVAILABLE",
                    )
                    wf.ensure_effect(
                        aggregate_type="workflow_run",
                        aggregate_id=str(run.id),
                        effect_type=OutboundEffectType.ASSIGNEE_PUBLIC_NOTICE,
                        idempotency_key=f"run:{run.id}:assignee-unavailable:v1",
                    )
                    notify = True
                else:
                    assignee = random.choice(pool)
                    wf.update_run(
                        run,
                        state=WorkflowState.ASSIGNEE_SELECTED,
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
                    for effect_type, suffix in (
                        (OutboundEffectType.ASSIGNEE_DM, "assignee-dm"),
                        (OutboundEffectType.ASSIGNEE_PUBLIC_NOTICE, "assignee-public"),
                    ):
                        wf.ensure_effect(
                            aggregate_type="workflow_run",
                            aggregate_id=str(run.id),
                            effect_type=effect_type,
                            idempotency_key=f"run:{run.id}:{suffix}:v1",
                        )
                    logger.info(
                        "assignee_selection_audit run_id=%s channel_id=%s selected=%s",
                        run.id,
                        slack_channel_id,
                        assignee,
                    )
                    notify = False
            else:
                notify = False
        if notify:
            self._post_effect(
                run_id=run_id,
                effect_type=OutboundEffectType.ASSIGNEE_PUBLIC_NOTICE,
                channel=slack_channel_id,
                thread_ts=thread_ts,
                message=render_assignee_unavailable(),
            )
            return
        self._deliver_assignee(run_id, slack_channel_id, thread_ts)

    def _calendar_details(
        self, run_id: int, slack_channel_id: str
    ) -> tuple[CalendarOutcome, str | None, str | None]:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if not run:
                return CalendarOutcome.FAILED, None, None
            ch = session.get(Channel, run.channel_id)
            if not ch or not run.winning_option_json:
                return CalendarOutcome.FAILED, None, None
            if run.calendar_outcome:
                return (
                    CalendarOutcome(run.calendar_outcome),
                    run.calendar_html_link,
                    run.calendar_operation_id,
                )
            operation_id = run.calendar_operation_id or (
                "tdb" + sha256(f"team-dinner:{run.id}".encode()).hexdigest()[:24]
            )
            wf.update_run(run, calendar_operation_id=operation_id)
            effect = wf.ensure_effect(
                aggregate_type="workflow_run",
                aggregate_id=str(run.id),
                effect_type=OutboundEffectType.CALENDAR_CREATE,
                idempotency_key=f"calendar:{operation_id}:v1",
            )
            if effect.status == OutboundEffectStatus.UNKNOWN:
                wf.update_run(run, calendar_outcome=CalendarOutcome.UNKNOWN)
                return CalendarOutcome.UNKNOWN, None, None
            if (
                effect.status == OutboundEffectStatus.PENDING
                and effect.attempt_count > 0
            ):
                wf.transition_with_effect(
                    run,
                    effect,
                    effect_status=OutboundEffectStatus.UNKNOWN,
                    error_code="CALENDAR_DELIVERY_UNRESOLVED",
                    calendar_outcome=CalendarOutcome.UNKNOWN,
                    result_code="CALENDAR_RESULT_UNKNOWN",
                )
                return CalendarOutcome.UNKNOWN, None, operation_id
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
            emails, missing_member_ids = collect_attendee_emails(
                session, self.client, required_slack_ids
            )
            optional_emails, optional_missing = collect_attendee_emails(
                session, self.client, optional_slack_ids
            )
            missing_member_ids.extend(optional_missing)
            winner = json.loads(run.winning_option_json)
            booking_url = ch.booking_url_template or m.MSG_BOOKING_URL_MISSING
            event = DinnerCalendarEvent(
                title=f"{m.BOT_NAME} 회식",
                dinner_date=date.fromisoformat(winner["date"]),
                tz_name=ch.tz,
                booking_url=None if booking_url == m.MSG_BOOKING_URL_MISSING else booking_url,
                attendee_emails=emails,
                optional_attendee_emails=optional_emails,
                missing_member_ids=missing_member_ids,
            )
            payload = build_google_calendar_event_payload(event)
            payload["id"] = operation_id
            fallback_url = build_google_calendar_url(event)
            wf.update_effect(
                effect,
                status=OutboundEffectStatus.PENDING,
                increment_attempt=True,
            )
        create_result = self.calendar_client.create_event(payload)
        outcome = create_result.outcome or (
            CalendarOutcome.CREATED if create_result.ok else CalendarOutcome.FAILED
        )
        if outcome == CalendarOutcome.CREATED:
            calendar_url = create_result.html_link
            effect_status = OutboundEffectStatus.SENT
            error_code = None
        elif outcome == CalendarOutcome.LINK_REQUIRED:
            calendar_url = fallback_url
            effect_status = OutboundEffectStatus.FAILED
            error_code = "CALENDAR_CONFIG_MISSING"
        elif outcome == CalendarOutcome.UNKNOWN:
            calendar_url = None
            effect_status = OutboundEffectStatus.UNKNOWN
            error_code = "CALENDAR_RESULT_UNKNOWN"
        else:
            calendar_url = fallback_url
            effect_status = OutboundEffectStatus.FAILED
            error_code = "CALENDAR_CREATE_FAILED"
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            effect = wf.get_effect(
                "workflow_run", str(run_id), OutboundEffectType.CALENDAR_CREATE
            )
            if run and effect:
                wf.transition_with_effect(
                    run,
                    effect,
                    effect_status=effect_status,
                    remote_ref=create_result.event_id or calendar_url,
                    error_code=error_code,
                    calendar_outcome=outcome,
                    calendar_event_id=create_result.event_id,
                    calendar_html_link=calendar_url,
                    result_code=error_code,
                )
        return outcome, calendar_url, operation_id

    def _deliver_assignee(
        self, run_id: int, slack_channel_id: str, thread_ts: str | None
    ) -> None:
        with self.session_factory() as session:
            wf = WorkflowRepository(session)
            run = wf.get_run(run_id)
            if (
                not run
                or run.state != WorkflowState.ASSIGNEE_SELECTED
                or not run.assignee_user_id
                or not run.winning_option_json
            ):
                return
            ch = session.get(Channel, run.channel_id)
            if not ch:
                return
            assignee = run.assignee_user_id
            winner = json.loads(run.winning_option_json)
            booking_url = ch.booking_url_template or m.MSG_BOOKING_URL_MISSING
            dm_effect = wf.get_effect(
                "workflow_run", str(run_id), OutboundEffectType.ASSIGNEE_DM
            )
            if dm_effect and dm_effect.status == OutboundEffectStatus.UNKNOWN:
                wf.update_run(
                    run,
                    state=WorkflowState.NEEDS_ATTENTION,
                    attention_reason="ASSIGNEE_DM_UNKNOWN",
                    result_code="ASSIGNEE_DM_UNKNOWN",
                )
                return
            if (
                dm_effect
                and dm_effect.status == OutboundEffectStatus.FAILED
                and dm_effect.attempt_count >= MAX_ASSIGNEE_DM_ATTEMPTS
            ):
                wf.update_run(
                    run,
                    state=WorkflowState.NEEDS_ATTENTION,
                    attention_reason="ASSIGNEE_DM_FAILED",
                    result_code="ASSIGNEE_DM_FAILED",
                )
                return
        calendar_outcome, calendar_url, correlation_id = self._calendar_details(
            run_id, slack_channel_id
        )
        dm_sent = self._post_effect(
            run_id=run_id,
            effect_type=OutboundEffectType.ASSIGNEE_DM,
            channel=assignee,
            message=render_assignee_dm(
                run_id=run_id,
                assignee=assignee,
                date_iso=winner["date"],
                booking_url=booking_url,
                calendar_outcome=calendar_outcome,
                calendar_url=calendar_url,
                correlation_id=correlation_id,
            ),
        )
        if not dm_sent:
            return
        with self.session_factory() as session:
            run = WorkflowRepository(session).get_run(run_id)
            if run:
                WorkflowRepository(session).record_assignee(
                    run.channel_id,
                    assignee,
                    run_id=run_id,
                )
        public_sent = self._post_effect(
            run_id=run_id,
            effect_type=OutboundEffectType.ASSIGNEE_PUBLIC_NOTICE,
            channel=slack_channel_id,
            thread_ts=thread_ts,
            message=render_assignee_public(assignee),
        )
        if not public_sent:
            with self.session_factory() as session:
                wf = WorkflowRepository(session)
                run = wf.get_run(run_id)
                effect = wf.get_effect(
                    "workflow_run",
                    str(run_id),
                    OutboundEffectType.ASSIGNEE_PUBLIC_NOTICE,
                )
                if run and effect and effect.status == OutboundEffectStatus.UNKNOWN:
                    wf.update_run(
                        run,
                        state=WorkflowState.NEEDS_ATTENTION,
                        attention_reason="ASSIGNEE_PUBLIC_UNKNOWN",
                        result_code="ASSIGNEE_PUBLIC_UNKNOWN",
                    )

    def resume_channel(self, slack_channel_id: str) -> None:
        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            if not ch:
                return
            run = WorkflowRepository(session).get_active_run(ch.id)
            if not run:
                return
            run_id = run.id
            thread_ts = run.thread_ts
        self._assign_booking(run_id, slack_channel_id, thread_ts)

    def recover_pending_runs(self) -> None:
        with self.session_factory() as session:
            runs = WorkflowRepository(session).list_nonterminal_runs()
            snapshot = [
                (
                    run.id,
                    run.state,
                    run.poll_deadline,
                    run.channel.channel_id,
                    run.thread_ts,
                )
                for run in runs
            ]
        for run_id, state, deadline, channel_id, thread_ts in snapshot:
            if state == WorkflowState.POLL_STARTING:
                self._post_open_poll(run_id, channel_id)
            elif state == WorkflowState.POLL_OPEN:
                if deadline:
                    with self.session_factory() as session:
                        run = WorkflowRepository(session).get_run(run_id)
                        ch = session.get(Channel, run.channel_id) if run else None
                    if ch:
                        normalized = self._normalize_deadline(deadline, ZoneInfo(ch.tz))
                        if normalized <= datetime.now(ZoneInfo(ch.tz)):
                            self.close_poll(run_id)
                        elif self._schedule_poll_close:
                            self._schedule_poll_close(run_id, normalized)
            elif state == WorkflowState.CLOSE_COMPUTED:
                self._deliver_poll_result(run_id, channel_id, thread_ts)
            elif state == WorkflowState.ASSIGNEE_SELECTED:
                self._deliver_assignee(run_id, channel_id, thread_ts)
            elif state == WorkflowState.NEEDS_ATTENTION:
                self.resume_channel(channel_id)

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
            if run.state != WorkflowState.ASSIGNEE_SELECTED:
                return m.MSG_BOOKING_NOT_READY
            if run.assignee_user_id and run.assignee_user_id != user_id:
                return m.MSG_ONLY_ASSIGNEE
            ch = session.get(Channel, run.channel_id)
            channel_id = ch.channel_id if ch else ""
            thread_ts = run.thread_ts
            wf.ensure_effect(
                aggregate_type="workflow_run",
                aggregate_id=str(run.id),
                effect_type=OutboundEffectType.BOOKING_DONE_NOTICE,
                idempotency_key=f"run:{run.id}:booking-done:v1",
            )
        if announce_channel and channel_id:
            delivered = self._post_effect(
                run_id=run_id,
                effect_type=OutboundEffectType.BOOKING_DONE_NOTICE,
                channel=channel_id,
                thread_ts=thread_ts,
                message=render_booking_done(user_id),
            )
            if not delivered:
                with self.session_factory() as session:
                    wf = WorkflowRepository(session)
                    run = wf.get_run(run_id)
                    effect = wf.get_effect(
                        "workflow_run",
                        str(run_id),
                        OutboundEffectType.BOOKING_DONE_NOTICE,
                    )
                    if run and effect and effect.status == OutboundEffectStatus.UNKNOWN:
                        wf.update_run(
                            run,
                            state=WorkflowState.NEEDS_ATTENTION,
                            attention_reason="BOOKING_DONE_NOTICE_UNKNOWN",
                            result_code="BOOKING_DONE_NOTICE_UNKNOWN",
                        )
                return m.MSG_BOOKING_DONE_PENDING
        with self.session_factory() as session:
            run = WorkflowRepository(session).get_run(run_id)
            if run:
                WorkflowRepository(session).update_run(
                    run,
                    state=WorkflowState.DONE,
                    terminal_reason="BOOKING_COMPLETED",
                )
        if self._cancel_poll_close:
            self._cancel_poll_close(run_id)
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
