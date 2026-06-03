# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.config import settings
from app.db.repository import ChannelRepository
from app.handlers.views import welcome_blocks
from app.schedule.spec import ScheduleSpec
from app.scheduler.runner import JobScheduler
from app.slack_invocation import USER_CMD
from app.workflow.engine import WorkflowEngine

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_MSG_PATTERNS = (
    re.compile(rf"^{re.escape(USER_CMD)}(?:\s+(.*))?$"),
    re.compile(rf"^/{re.escape(USER_CMD)}(?:\s+(.*))?$"),
    re.compile(rf"^\s/{re.escape(USER_CMD)}(?:\s+(.*))?$"),
)


def normalize_invocation_text(raw: str) -> str | None:
    """Return subcommand tail if text is a \ud68c\uc2dd invocation, else None."""
    text = _MENTION_RE.sub("", raw).strip()
    for pattern in _MSG_PATTERNS:
        match = pattern.match(text)
        if match:
            return (match.group(1) or "").strip()
    return None


def format_status(channel_id: str, session_factory: sessionmaker) -> str:
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(channel_id)
    if not ch or not ch.schedule_json:
        return m.MSG_NO_SCHEDULE
    spec = ScheduleSpec.model_validate_json(ch.schedule_json)
    nxt = spec.next_run_after(datetime.now(), ch.tz)
    return (
        f"{m.MSG_STATUS_HEADER}\n"
        f"\uC77C\uC815: {spec.describe_ko()}\n"
        f"\uB2E4\uC74C \uC2E4\uD589: {nxt.strftime('%Y-%m-%d %H:%M')} ({ch.tz})\n"
        f"\uD22C\uD45C: {ch.poll_duration_hours}\uC2DC\uAC04\n"
        "Google 캘린더: 설정 시 직접 생성, 미설정 시 예약 담당자 DM에 생성 링크 제공"
    )


def help_text() -> str:
    return (
        f"*{m.BOT_NAME} \uC0AC\uC6A9 \uBC29\uBC95*\n"
        f"\u2022 `{USER_CMD}` \u2014 \uC77C\uC815 \uC124\uC815 (\uBC84\uD2BC \uD45C\uC2DC)\n"
        f"\u2022 `@봇이름 {USER_CMD}` \u2014 \uB3D9\uC77C\n"
        f"\u2022 ` /{USER_CMD}` \u2014 \uC2AC\uB79C\uC2DC \uBA85\uB839 \uB300\uC2E0 \uBA54\uC2DC\uC9C0\uB85C \uC0AC\uC6A9 (\uC55E\uC5D0 \uACF5\uBC31)\n"
        f"\u2022 *{m.BTN_SETTINGS}* \uBC84\uD2BC \u2014 \uC124\uC815 \uBAA8\uB2EC\n"
        f"\u2022 `{USER_CMD} \uC0C1\uD0DC` / `{USER_CMD} status` \u2014 \uC77C\uC815 \uD655\uC778\n"
        f"\u2022 `{USER_CMD} \uC124\uC815` / `{USER_CMD} settings` \u2014 \uC124\uC815 \uBC84\uD2BC \uD45C\uC2DC\n"
        f"\u2022 `{USER_CMD} \uC9C0\uAE08` / `{USER_CMD} run-now` \u2014 \uC989\uC2DC \uD22C\uD45C (\uAD00\uB9AC\uC790)\n"
        f"\u2022 `{USER_CMD} \uCDE8\uC18C` / `{USER_CMD} cancel` \u2014 \uC9C4\uD589 \uC911\uC778 \uD68C\uC2DD \uCDE8\uC18C (\uAD00\uB9AC\uC790)\n"
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
        reply(format_status(channel_id, session_factory))
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


def register_natural_language_handlers(
    app,
    session_factory: sessionmaker,
    engine: WorkflowEngine,
    job_scheduler: JobScheduler | None = None,
) -> None:
    def _reply_in_channel(client, channel_id: str, text: str, thread_ts: str | None) -> None:
        client.chat_postMessage(channel=channel_id, text=text, thread_ts=thread_ts)

    def _prompt_buttons(client, channel_id: str, thread_ts: str | None) -> None:
        client.chat_postMessage(
            channel=channel_id,
            text=m.MSG_SETTINGS_PROMPT,
            blocks=welcome_blocks(),
            thread_ts=thread_ts,
        )

    @app.event("app_mention")
    def on_app_mention(event, client, logger):
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return
        channel_id = event["channel"]
        user_id = event["user"]
        thread_ts = event.get("ts")
        raw = event.get("text") or ""
        logger.info("app_mention from %s in %s: %r", user_id, channel_id, raw)
        sub_text = normalize_invocation_text(raw)
        if sub_text is None:
            tail = _MENTION_RE.sub("", raw).strip()
            sub_text = tail

        try:
            dispatch_hoesik_intent(
                sub_text=sub_text,
                channel_id=channel_id,
                user_id=user_id,
                session_factory=session_factory,
                engine=engine,
                job_scheduler=job_scheduler,
                reply=lambda msg: _reply_in_channel(client, channel_id, msg, thread_ts),
                open_modal=None,
                post_action_prompt=lambda: _prompt_buttons(client, channel_id, thread_ts),
            )
        except Exception:
            logger.exception("app_mention handler failed")
            _reply_in_channel(
                client,
                channel_id,
                "\uCC98\uB9AC \uC911 \uC624\uB958\uAC00 \uBC1C\uC0DD\uD588\uC2B5\uB2C8\uB2E4. \uC7A0\uC2DC \uD6C4 \uB2E4\uC2DC \uC2DC\uB3C4\uD574 \uC8FC\uC138\uC694.",
                thread_ts,
            )

    def _is_hoesik_message(message: dict) -> bool:
        if message.get("bot_id") or message.get("subtype"):
            return False
        text = message.get("text") or ""
        # app_mention handler also fires for @bot text; avoid duplicate replies
        if _MENTION_RE.search(text):
            return False
        return normalize_invocation_text(text) is not None

    @app.message(matchers=[_is_hoesik_message])
    def on_hoesik_message(message, client, logger):
        channel_id = message["channel"]
        user_id = message["user"]
        thread_ts = message.get("ts")
        sub_text = normalize_invocation_text(message.get("text") or "") or ""
        logger.info("hoesik message from %s in %s: %r", user_id, channel_id, message.get("text"))

        try:
            dispatch_hoesik_intent(
                sub_text=sub_text,
                channel_id=channel_id,
                user_id=user_id,
                session_factory=session_factory,
                engine=engine,
                job_scheduler=job_scheduler,
                reply=lambda msg: _reply_in_channel(client, channel_id, msg, thread_ts),
                open_modal=None,
                post_action_prompt=lambda: _prompt_buttons(client, channel_id, thread_ts),
            )
        except Exception:
            logger.exception("hoesik message handler failed")
            _reply_in_channel(
                client,
                channel_id,
                "\uCC98\uB9AC \uC911 \uC624\uB958\uAC00 \uBC1C\uC0DD\uD588\uC2B5\uB2C8\uB2E4. \uC7A0\uC2DC \uD6C4 \uB2E4\uC2DC \uC2DC\uB3C4\uD574 \uC8FC\uC138\uC694.",
                thread_ts,
            )
