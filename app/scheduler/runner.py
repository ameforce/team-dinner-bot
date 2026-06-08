# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import sessionmaker
from zoneinfo import ZoneInfo

from app.config import settings
from app.settings_defaults import clamp_poll_duration_hours
from app.db.repository import ChannelRepository, WorkflowRepository
from app.schedule.spec import ScheduleSpec
from app.workflow.engine import WorkflowEngine

logger = logging.getLogger(__name__)

POLL_CLOSE_JOB_PREFIX = "poll_close_"


class JobScheduler:
    def __init__(self, session_factory: sessionmaker, engine: WorkflowEngine):
        self.session_factory = session_factory
        self.engine = engine
        self.scheduler = BackgroundScheduler(timezone=settings.default_timezone)
        engine.bind_poll_scheduler(self.schedule_poll_close, self.cancel_poll_close)

    def start(self) -> None:
        self.refresh_all()
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
                        hours = clamp_poll_duration_hours(ch.poll_duration_hours)
                        deadline = now + timedelta(hours=hours)
                        with self.session_factory() as session:
                            row = WorkflowRepository(session).get_run(run.id)
                            if row:
                                WorkflowRepository(session).update_run(
                                    row, poll_deadline=deadline
                                )
                        logger.warning(
                            "poll_close run_id=%s had past deadline; rescheduled to %s",
                            run.id,
                            deadline,
                        )
                    self.schedule_poll_close(run.id, deadline)

    def refresh_all(self) -> None:
        with self.session_factory() as session:
            channels = ChannelRepository(session).list_enabled_with_schedule()
        for ch in channels:
            self.schedule_channel(ch.channel_id)

    def schedule_channel(self, slack_channel_id: str) -> None:
        job_id = f"channel_run_{slack_channel_id}"
        for job in list(self.scheduler.get_jobs()):
            if job.id == job_id:
                self.scheduler.remove_job(job.id)

        with self.session_factory() as session:
            ch = ChannelRepository(session).get_by_channel_id(slack_channel_id)
            if (
                not ch
                or not ch.schedule_json
                or not ch.enabled
                or not ch.automatic_execution_enabled
            ):
                return
            spec = ScheduleSpec.model_validate_json(ch.schedule_json)
            tz = ZoneInfo(ch.tz)
            next_run = spec.next_run_after(datetime.now(tz), ch.tz)

        self.scheduler.add_job(
            self._trigger_run,
            trigger=DateTrigger(run_date=next_run),
            id=job_id,
            kwargs={"slack_channel_id": slack_channel_id},
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("Scheduled %s at %s", job_id, next_run)

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
