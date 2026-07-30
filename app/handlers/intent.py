# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.config import settings
from app.db.repository import ChannelRepository
from app.schedule.spec import ScheduleSpec
from app.rendering import render_status
from app.scheduler.runner import JobScheduler
from app.slack_invocation import USER_CMD
from app.workflow.engine import WorkflowEngine


def format_status(
    channel_id: str,
    session_factory: sessionmaker,
    job_scheduler: JobScheduler | None = None,
) -> str:
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(channel_id)
    if not ch or not ch.schedule_json:
        return m.MSG_NO_SCHEDULE
    spec = ScheduleSpec.model_validate_json(ch.schedule_json)
    scheduler_state = job_scheduler.read_channel(channel_id) if job_scheduler else None
    return render_status(
        schedule_description=spec.describe_ko(),
        automatic_enabled=ch.automatic_execution_enabled,
        poll_duration_hours=ch.poll_duration_hours,
        timezone_name=ch.tz,
        scheduler_applied=bool(scheduler_state and scheduler_state.applied),
        next_run=scheduler_state.next_run if scheduler_state else None,
    ).text


def help_text() -> str:
    return (
        f"*{m.BOT_NAME} \uC0AC\uC6A9 \uBC29\uBC95*\n"
        f"\u2022 `/{USER_CMD}` \u2014 \uC124\uC815/\uC0C1\uD0DC/\uC2E4\uD589 \uD328\uB110\n"
        f"\u2022 *{m.BTN_SETTINGS}* \uBC84\uD2BC \u2014 \uC124\uC815 \uBAA8\uB2EC\n"
        f"\u2022 `/{USER_CMD} \uC0C1\uD0DC` / `/{USER_CMD} status` \u2014 \uC77C\uC815 \uD655\uC778\n"
        f"\u2022 `/{USER_CMD} \uC124\uC815` / `/{USER_CMD} settings` \u2014 \uC124\uC815 \uBAA8\uB2EC\n"
        f"\u2022 `/{USER_CMD} \uC9C0\uAE08` / `/{USER_CMD} run-now` \u2014 \uC989\uC2DC \uD22C\uD45C (\uAD00\uB9AC\uC790)\n"
        f"\u2022 `/{USER_CMD} \uCDE8\uC18C` / `/{USER_CMD} cancel` \u2014 \uC9C4\uD589 \uC911\uC778 \uD68C\uC2DD \uCDE8\uC18C (\uAD00\uB9AC\uC790)\n"
        "\u2022 Google \uce98\ub9b0\ub354 \ub9c1\ud06c\ub294 \uc608\uc57d \ub2f4\ub2f9\uc790 DM\uc5d0 \uc790\ub3d9\uc73c\ub85c \uc81c\uacf5"
    )


def _calendar_link_help() -> str:
    return (
        "Google 캘린더는 인증 설정이 있으면 직접 일정을 생성합니다. "
        "설정이 없거나 직접 생성할 수 없으면 예약 담당자 DM에 현재 브라우저 Google 계정으로 여는 "
        "캘린더 생성 링크를 대체 링크로 제공합니다."
    )


def dispatch_hoesik_intent(
    *,
    sub_text: str,
    channel_id: str,
    user_id: str,
    session_factory: sessionmaker,
    engine: WorkflowEngine,
    job_scheduler: JobScheduler | None,
    reply: Callable[[str], None],
    open_modal: Callable[[], None] | None,
    post_action_prompt: Callable[[], None] | None,
) -> None:
    parts = sub_text.split()
    sub = parts[0].lower() if parts else ""

    if sub in ("status", "\uc0c1\ud0dc", "\uc77c\uc815"):
        reply(format_status(channel_id, session_factory, job_scheduler))
        if post_action_prompt is not None:
            post_action_prompt()
        return
    if sub in ("settings", "setting", "\uc124\uc815"):
        if post_action_prompt is not None:
            post_action_prompt()
            return
        reply(m.MSG_USE_SETTINGS_BUTTON)
        return
    if sub in ("run-now", "run", "\uc9c0\uae08", "\ud14c\uc2a4\ud2b8"):
        if settings.admin_ids and user_id not in settings.admin_ids:
            reply(m.MSG_ADMIN_ONLY)
            return
        replace = len(parts) > 1 and parts[1].lower() in (
            "\uac15\uc81c",
            "replace",
            "force",
            "reset",
        )
        err = engine.start_channel_run(channel_id, replace=replace)
        if err:
            reply(err)
            return
        reply(m.MSG_POLL_START_REQUESTED)
        return
    if sub in ("cancel", "\ucde8\uc18c"):
        if settings.admin_ids and user_id not in settings.admin_ids:
            reply(m.MSG_ADMIN_ONLY)
            return
        reply(engine.cancel_current_run(channel_id))
        return
    if sub in ("google-auth", "google", "\uad6c\uae00", "google-code", "google_code", "\uad6c\uae00\ucf54\ub4dc"):
        reply(_calendar_link_help())
        return
    if sub in ("help", "\ub3c4\uc6c0\ub9d0", "?"):
        reply(help_text())
        return

    if open_modal is not None:
        open_modal()
        return
    if post_action_prompt is not None:
        post_action_prompt()
        return
    reply(m.MSG_USE_SETTINGS_BUTTON)
