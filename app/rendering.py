# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from app import messages as m
from app.workflow.dates import format_date_ko
from app.workflow.states import CalendarOutcome


class MessageAudience(StrEnum):
    PUBLIC = "PUBLIC"
    EPHEMERAL = "EPHEMERAL"
    DM = "DM"


class ResultCode(StrEnum):
    SETTINGS_SAVED = "SETTINGS_SAVED"
    SETTINGS_SAVED_SCHEDULER_PENDING = "SETTINGS_SAVED_SCHEDULER_PENDING"
    SETTINGS_INVALID = "SETTINGS_INVALID"
    SETTINGS_FAILED = "SETTINGS_FAILED"
    WELCOME_READY = "WELCOME_READY"
    WELCOME_SCHEDULER_PENDING = "WELCOME_SCHEDULER_PENDING"
    STATUS = "STATUS"
    POLL_OPEN = "POLL_OPEN"
    POLL_RESULT = "POLL_RESULT"
    ASSIGNEE_SELECTED = "ASSIGNEE_SELECTED"
    ASSIGNEE_UNAVAILABLE = "ASSIGNEE_UNAVAILABLE"
    BOOKING_DONE = "BOOKING_DONE"
    OPERATION_FAILED = "OPERATION_FAILED"


@dataclass(frozen=True)
class RenderedSlackMessage:
    result_code: ResultCode
    audience: MessageAudience
    text: str
    blocks: list[dict[str, Any]] | None = None


def post_rendered(client, channel: str, message: RenderedSlackMessage, **kwargs):
    payload = {"channel": channel, "text": message.text, **kwargs}
    if message.blocks is not None:
        payload["blocks"] = message.blocks
    if message.audience == MessageAudience.EPHEMERAL:
        user = payload.pop("user")
        return client.chat_postEphemeral(user=user, **payload)
    return client.chat_postMessage(**payload)


def update_rendered(client, channel: str, ts: str, message: RenderedSlackMessage):
    payload = {"channel": channel, "ts": ts, "text": message.text}
    if message.blocks is not None:
        payload["blocks"] = message.blocks
    return client.chat_update(**payload)


def render_settings_saved(
    *,
    automatic_enabled: bool,
    schedule_description: str,
    poll_duration_hours: int,
    scheduler_applied: bool,
    next_run: datetime | None,
) -> RenderedSlackMessage:
    result_code = (
        ResultCode.SETTINGS_SAVED
        if scheduler_applied
        else ResultCode.SETTINGS_SAVED_SCHEDULER_PENDING
    )
    lines = [
        m.MSG_SAVED,
        f"• 자동 실행: {'사용' if automatic_enabled else '사용 안 함'}",
    ]
    if automatic_enabled:
        lines.append(f"• 실행 일정: {schedule_description}")
        if scheduler_applied and next_run is not None:
            lines.append(f"• 다음 실행: {next_run.strftime('%Y-%m-%d %H:%M')}")
        else:
            lines.append("• 자동 실행 반영 지연: scheduler 상태를 다시 확인해 주세요.")
    elif not scheduler_applied:
        lines.append(
            "• 자동 실행 중지 반영 지연: scheduler 상태를 다시 확인해 주세요."
        )
    lines.append(f"• 투표 마감: 시작 후 {poll_duration_hours}시간")
    return RenderedSlackMessage(
        result_code=result_code,
        audience=MessageAudience.PUBLIC,
        text="\n".join(lines),
    )


def render_settings_failed(correlation_id: str) -> RenderedSlackMessage:
    return RenderedSlackMessage(
        ResultCode.SETTINGS_FAILED,
        MessageAudience.EPHEMERAL,
        (
            "설정을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.\n"
            f"• 오류 코드: SETTINGS_FAILED\n• 추적 ID: {correlation_id}"
        ),
    )


def render_status(
    *,
    schedule_description: str,
    automatic_enabled: bool,
    poll_duration_hours: int,
    timezone_name: str,
    scheduler_applied: bool,
    next_run: datetime | None,
) -> RenderedSlackMessage:
    lines = [
        m.MSG_STATUS_HEADER,
        f"• 자동 실행: {'사용' if automatic_enabled else '사용 안 함'}",
    ]
    if automatic_enabled:
        lines.append(f"• 실행 일정: {schedule_description}")
        if scheduler_applied and next_run is not None:
            lines.append(
                f"• 다음 실행: {next_run.strftime('%Y-%m-%d %H:%M')} ({timezone_name})"
            )
        else:
            lines.append("• 자동 실행 반영 지연: scheduler job을 확인할 수 없습니다.")
    else:
        if not scheduler_applied:
            lines.append(
                "• 자동 실행 중지 반영 지연: scheduler job이 남아 있거나 상태를 확인할 수 없습니다."
            )
        lines.append(f"• 재활성화 시 사용할 일정: {schedule_description}")
    lines.extend(
        [
            f"• 투표 마감: 시작 후 {poll_duration_hours}시간",
            "• Google 캘린더: 직접 생성 또는 담당자용 생성 링크 제공",
        ]
    )
    return RenderedSlackMessage(
        result_code=ResultCode.STATUS,
        audience=MessageAudience.EPHEMERAL,
        text="\n".join(lines),
    )


def render_welcome(*, scheduler_applied: bool, automatic_enabled: bool) -> RenderedSlackMessage:
    neutral_text = m.WELCOME_TEXT.replace(
        "이 채널에서 회식 일정을 자동으로 관리합니다.",
        "이 채널의 회식 일정 설정을 도와드립니다.",
    )
    if not automatic_enabled:
        if not scheduler_applied:
            return RenderedSlackMessage(
                ResultCode.WELCOME_SCHEDULER_PENDING,
                MessageAudience.PUBLIC,
                (
                    f"{neutral_text}\n자동 실행: 사용 안 함\n"
                    "자동 실행 중지 반영이 확인되지 않았습니다. 설정에서 상태를 다시 확인해 주세요."
                ),
            )
        return RenderedSlackMessage(
            ResultCode.WELCOME_READY,
            MessageAudience.PUBLIC,
            f"{neutral_text}\n자동 실행: 사용 안 함",
        )
    if automatic_enabled and not scheduler_applied:
        text = (
            f"{neutral_text}\n"
            "자동 실행 scheduler 반영이 지연되었습니다. 설정에서 상태를 다시 확인해 주세요."
        )
        code = ResultCode.WELCOME_SCHEDULER_PENDING
    else:
        text = m.WELCOME_TEXT
        code = ResultCode.WELCOME_READY
    return RenderedSlackMessage(code, MessageAudience.PUBLIC, text)


def render_poll_open(*, blocks: list[dict[str, Any]]) -> RenderedSlackMessage:
    return RenderedSlackMessage(
        ResultCode.POLL_OPEN,
        MessageAudience.PUBLIC,
        m.MSG_POLL_STARTED,
        blocks,
    )


def render_poll_result(date_iso: str, counts: dict[str, int]) -> RenderedSlackMessage:
    winner = format_date_ko(date.fromisoformat(date_iso))
    lines = [f"*투표가 마감되었습니다.*\n확정일: *{winner}* (`{date_iso}`)"]
    if counts:
        lines.append("\n*날짜별 불가능 응답*")
        for iso, count in sorted(counts.items(), key=lambda item: (item[1], item[0])):
            label = format_date_ko(date.fromisoformat(iso))
            lines.append(f"• {label}: {count}명")
    return RenderedSlackMessage(
        ResultCode.POLL_RESULT,
        MessageAudience.PUBLIC,
        "\n".join(lines),
    )


def render_assignee_unavailable() -> RenderedSlackMessage:
    return RenderedSlackMessage(
        ResultCode.ASSIGNEE_UNAVAILABLE,
        MessageAudience.PUBLIC,
        (
            "예약 담당자를 지정할 수 없어 이 회차를 보류했습니다.\n"
            "참여자 설정을 보완하면 같은 확정일로 이어서 진행할 수 있습니다."
        ),
    )


def render_assignee_public(assignee: str) -> RenderedSlackMessage:
    return RenderedSlackMessage(
        ResultCode.ASSIGNEE_SELECTED,
        MessageAudience.PUBLIC,
        f"<@{assignee}> 님이 이번 회식 예약 담당입니다.",
    )


def render_assignee_dm(
    *,
    run_id: int,
    assignee: str,
    date_iso: str,
    booking_url: str,
    calendar_outcome: CalendarOutcome,
    calendar_url: str | None,
    correlation_id: str | None = None,
) -> RenderedSlackMessage:
    if calendar_outcome == CalendarOutcome.CREATED:
        calendar_line = (
            f"Google 캘린더 이벤트: {calendar_url}"
            if calendar_url
            else "Google 캘린더 이벤트: 생성됨 (링크 미제공)"
        )
    elif calendar_outcome == CalendarOutcome.LINK_REQUIRED:
        calendar_line = f"Google 캘린더 생성 링크: {calendar_url}"
    elif calendar_outcome == CalendarOutcome.UNKNOWN:
        calendar_line = (
            "Google 캘린더 생성 결과를 확인할 수 없습니다. 자동 재시도하지 않습니다."
        )
    else:
        suffix = f" (오류 코드: CALENDAR_FAILED, 추적 ID: {correlation_id})" if correlation_id else ""
        calendar_line = f"Google 캘린더 직접 생성에 실패했습니다.{suffix}"
        if calendar_url:
            calendar_line += f"\nGoogle 캘린더 생성 링크: {calendar_url}"
    text = (
        f"{m.MSG_BOOKING_DM_TITLE}\n"
        f"확정일: `{date_iso}`\n"
        f"예약 링크: {booking_url}\n"
        f"{calendar_line}\n"
        "예약 완료 후 아래 버튼을 눌러 주세요."
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
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
    ]
    return RenderedSlackMessage(ResultCode.ASSIGNEE_SELECTED, MessageAudience.DM, text, blocks)


def render_booking_done(user_id: str) -> RenderedSlackMessage:
    return RenderedSlackMessage(
        ResultCode.BOOKING_DONE,
        MessageAudience.PUBLIC,
        (
            f"<@{user_id}> 님이 예약을 완료했습니다. "
            "이번 회식 일정 흐름이 종료되었습니다."
        ),
    )
