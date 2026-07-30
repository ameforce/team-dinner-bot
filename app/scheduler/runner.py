# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import sessionmaker
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.repository import ChannelRepository, WorkflowRepository
from app.schedule.spec import ScheduleSpec
from app.workflow.engine import WorkflowEngine
from app.workflow.states import OutboundEffectStatus, OutboundEffectType

logger = logging.getLogger(__name__)

POLL_CLOSE_JOB_PREFIX = "poll_close_"


@dataclass(frozen=True)
class SchedulerSyncResult:
    automatic_enabled: bool
    applied: bool
    next_run: datetime | None = None
    error_code: str | None = None


class JobScheduler:
    def __init__(self, session_factory: sessionmaker, engine: WorkflowEngine):
        self.session_factory = session_factory
        self.engine = engine
        self.scheduler = BackgroundScheduler(timezone=settings.default_timezone)
        engine.bind_poll_scheduler(self.schedule_poll_close, self.cancel_poll_close)

    def start(self) -> None:
        self.refresh_all()
        self.engine.recover_pending_runs()
        self.reschedule_open_poll_closes()
        self.scheduler.start()
        logger.info("APScheduler started")

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    def poll_close_job_id(self, run_id: int) -> str:
        return f"{POLL_CLOSE_JOB_PREFIX}{run_id}"

    def cancel_poll_close(self, run_id: int) -> None:
        job_id = self.poll_close_job_id(run_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def cancel_poll_close_for_channel(self, slack_channel_id: str) -> None:
        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            if not ch:
                return
            runs = WorkflowRepository(session).list_open_runs(ch.id)
        for run in runs:
            self.cancel_poll_close(run.id)

    def reschedule_open_poll_closes(self) -> None:
        """Re-arm poll close timers after process restart (in-memory scheduler)."""
        tz = ZoneInfo(settings.default_timezone)
        now = datetime.now(tz)
        with self.session_factory() as session:
            channels = ChannelRepository(session).list_enabled_with_schedule()
            for ch in channels:
                for run in WorkflowRepository(session).list_open_runs(ch.id):
                    if not run.poll_deadline:
                        continue
                    deadline = WorkflowEngine._normalize_deadline(
                        run.poll_deadline,
                        ZoneInfo(ch.tz),
                    )
                    if deadline <= now:
                        logger.warning(
                            "poll_close run_id=%s overdue at %s; closing immediately",
                            run.id,
                            deadline,
                        )
                        self.engine.close_poll(run.id)
                    else:
                        self.schedule_poll_close(run.id, deadline)

    def refresh_all(self) -> None:
        with self.session_factory() as session:
            channels = ChannelRepository(session).list_enabled_with_schedule()
        for ch in channels:
            self.schedule_channel(ch.channel_id)

    def schedule_channel(self, slack_channel_id: str) -> SchedulerSyncResult:
        job_id = f"channel_run_{slack_channel_id}"
        for job in list(self.scheduler.get_jobs()):
            if job.id == job_id:
                self.scheduler.remove_job(job.id)

        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            revision = ch.settings_revision if ch else 0
            effect = WorkflowRepository(session).ensure_effect(
                aggregate_type="channel",
                aggregate_id=slack_channel_id,
                effect_type=OutboundEffectType.SCHEDULER_SYNC,
                idempotency_key=f"channel:{slack_channel_id}:scheduler:{revision}:v1",
            )
            if (
                not ch
                or not ch.schedule_json
                or not ch.enabled
                or not ch.automatic_execution_enabled
            ):
                applied = self.scheduler.get_job(job_id) is None
                WorkflowRepository(session).update_effect(
                    effect,
                    status=(
                        OutboundEffectStatus.SENT
                        if applied
                        else OutboundEffectStatus.FAILED
                    ),
                    error_code=None if applied else "SCHEDULER_REMOVE_FAILED",
                    increment_attempt=True,
                )
                return SchedulerSyncResult(
                    automatic_enabled=bool(ch and ch.automatic_execution_enabled),
                    applied=applied,
                    error_code=None if applied else "SCHEDULER_REMOVE_FAILED",
                )
            spec = ScheduleSpec.model_validate_json(ch.schedule_json)
            tz = ZoneInfo(ch.tz)
            next_run = spec.next_run_after(datetime.now(tz), ch.tz)

        try:
            self.scheduler.add_job(
                self._trigger_run,
                trigger=DateTrigger(run_date=next_run),
                id=job_id,
                kwargs={"slack_channel_id": slack_channel_id},
                replace_existing=True,
                misfire_grace_time=3600,
            )
            read_back = self.scheduler.get_job(job_id)
            read_back_next = getattr(read_back, "next_run_time", None)
            applied = bool(
                read_back
                and (read_back_next is None or read_back_next == next_run)
            )
        except Exception:
            logger.exception("Failed to synchronize scheduler job %s", job_id)
            applied = False
            read_back = None
            read_back_next = None
        with self.session_factory() as session:
            effect = WorkflowRepository(session).get_effect(
                "channel", slack_channel_id, OutboundEffectType.SCHEDULER_SYNC
            )
            if effect:
                WorkflowRepository(session).update_effect(
                    effect,
                    status=(
                        OutboundEffectStatus.SENT
                        if applied
                        else OutboundEffectStatus.FAILED
                    ),
                    remote_ref=job_id if applied else None,
                    error_code=None if applied else "SCHEDULER_SYNC_FAILED",
                    increment_attempt=True,
                )
        logger.info("Scheduled %s at %s", job_id, next_run)
        return SchedulerSyncResult(
            automatic_enabled=True,
            applied=applied,
            next_run=(read_back_next or next_run) if read_back and applied else None,
            error_code=None if applied else "SCHEDULER_SYNC_FAILED",
        )

    def read_channel(self, slack_channel_id: str) -> SchedulerSyncResult:
        job_id = f"channel_run_{slack_channel_id}"
        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
        if not ch or not ch.enabled or not ch.automatic_execution_enabled:
            return SchedulerSyncResult(
                automatic_enabled=bool(ch and ch.automatic_execution_enabled),
                applied=self.scheduler.get_job(job_id) is None,
            )
        job = self.scheduler.get_job(job_id)
        return SchedulerSyncResult(
            automatic_enabled=True,
            applied=job is not None,
            next_run=getattr(job, "next_run_time", None) if job else None,
            error_code=None if job else "SCHEDULER_JOB_MISSING",
        )

    def schedule_poll_close(self, run_id: int, deadline: datetime) -> None:
        tz = ZoneInfo(settings.default_timezone)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=tz)
        else:
            deadline = deadline.astimezone(tz)
        now = datetime.now(tz)
        if deadline <= now:
            logger.warning(
                "poll_close run_id=%s deadline not in future (%s); skipping timer",
                run_id,
                deadline,
            )
            return
        job_id = self.poll_close_job_id(run_id)
        self.scheduler.add_job(
            self.engine.close_poll,
            trigger=DateTrigger(run_date=deadline),
            id=job_id,
            kwargs={"run_id": run_id},
            replace_existing=True,
            misfire_grace_time=600,
        )
        logger.info("Scheduled %s at %s", job_id, deadline)

    def _trigger_run(self, slack_channel_id: str) -> None:
        err = self.engine.start_channel_run(slack_channel_id)
        if err:
            logger.warning("start_channel_run(%s): %s", slack_channel_id, err)
        self.schedule_channel(slack_channel_id)
