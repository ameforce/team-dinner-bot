# -*- coding: utf-8 -*-
"""Slash command dispatch branches."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Channel
from app.handlers.intent import (
    dispatch_hoesik_intent,
    format_status,
    help_text,
)
from app.schedule.spec import ScheduleSpec, ScheduleType
from app.slack_invocation import USER_CMD

def test_help_text_contains_commands():
    text = help_text()
    assert f"/{USER_CMD}" in text
    assert "status" in text or "\uc0c1\ud0dc" in text
    assert "@봇이름" not in text
    assert f" /{USER_CMD}" not in text
    assert "OAuth" not in text
    assert "google-code" not in text


def test_intent_module_exposes_no_natural_language_registration():
    import app.handlers.intent as intent

    assert not hasattr(intent, "normalize_invocation_text")
    assert not hasattr(intent, "register_natural_language_handlers")


def _dispatch(sub_text: str, *, admin_user_ids: str = "", user_id: str = "U1"):
    replies: list[str] = []
    prompts: list[bool] = []
    engine = MagicMock()
    engine.start_channel_run.return_value = None
    session_factory = MagicMock()
    session = MagicMock()
    session_factory.return_value.__enter__.return_value = session
    ch_repo = MagicMock()
    ch_repo.get_by_channel_id.return_value = None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.handlers.intent.settings.admin_user_ids", admin_user_ids)
        dispatch_hoesik_intent(
            sub_text=sub_text,
            channel_id="C_TEST",
            user_id=user_id,
            session_factory=session_factory,
            engine=engine,
            job_scheduler=None,
            reply=replies.append,
            open_modal=None,
            post_action_prompt=lambda: prompts.append(True),
        )
    return replies, prompts, engine


def test_dispatch_default_shows_buttons():
    _, prompts, _ = _dispatch("")
    assert prompts == [True]


def test_dispatch_help():
    replies, _, _ = _dispatch("help")
    assert USER_CMD in replies[0]


def test_dispatch_run_now_admin_gate():
    replies, _, engine = _dispatch("\uc9c0\uae08", admin_user_ids="U_ADMIN", user_id="U_OTHER")
    assert "\uad00\ub9ac\uc790" in replies[0]
    engine.start_channel_run.assert_not_called()

    replies2, _, engine2 = _dispatch("\uc9c0\uae08", admin_user_ids="U_ADMIN", user_id="U_ADMIN")
    assert "\ud22c\ud45c" in replies2[0]
    engine2.start_channel_run.assert_called_once()


def test_dispatch_run_now_no_admin_list():
    replies, _, engine = _dispatch("run-now", admin_user_ids="")
    assert "\ud22c\ud45c" in replies[0]
    engine.start_channel_run.assert_called_once_with("C_TEST", replace=False)


def test_dispatch_cancel_admin_gate():
    replies, _, engine = _dispatch("취소", admin_user_ids="U_ADMIN", user_id="U_OTHER")
    assert "관리자" in replies[0]
    engine.cancel_current_run.assert_not_called()

    engine2 = MagicMock()
    engine2.cancel_current_run.return_value = "cancel ok"
    replies2: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.handlers.intent.settings.admin_user_ids", "U_ADMIN")
        dispatch_hoesik_intent(
            sub_text="cancel",
            channel_id="C_TEST",
            user_id="U_ADMIN",
            session_factory=MagicMock(),
            engine=engine2,
            job_scheduler=None,
            reply=replies2.append,
            open_modal=None,
            post_action_prompt=lambda: None,
        )
    assert replies2 == ["cancel ok"]
    engine2.cancel_current_run.assert_called_once_with("C_TEST")


def test_dispatch_settings_subcommand_shows_action_panel():
    _replies, prompts, _engine = _dispatch("settings")
    assert prompts == [True]


def test_dispatch_status_subcommand_also_shows_action_panel(monkeypatch):
    monkeypatch.setattr("app.handlers.intent.format_status", lambda *_args: "status ok")

    replies, prompts, _engine = _dispatch("status")

    assert replies == ["status ok"]
    assert prompts == [True]


def test_format_status_reports_automatic_execution_off_without_next_run(tmp_path):
    db = tmp_path / "status.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    spec = ScheduleSpec(type=ScheduleType.WEEKLY_WEEKDAY, weekday=1, hour=10, minute=0)
    with factory() as session:
        session.add(
            Channel(
                team_id="T1",
                channel_id="C_STATUS",
                enabled=True,
                automatic_execution_enabled=False,
                schedule_json=spec.model_dump_json(),
                poll_duration_hours=48,
                tz="Asia/Seoul",
            )
        )
        session.commit()

    text = format_status("C_STATUS", factory)

    assert "자동 실행: 사용 안 함" in text
    assert "다음 실행:" not in text


def test_dispatch_engine_error():
    engine = MagicMock()
    engine.start_channel_run.return_value = "\uc774\ubbf8 \uc9c4\ud589 \uc911"
    replies: list[str] = []

    dispatch_hoesik_intent(
        sub_text="\uc9c0\uae08",
        channel_id="C_TEST",
        user_id="U1",
        session_factory=MagicMock(),
        engine=engine,
        job_scheduler=None,
        reply=replies.append,
        open_modal=None,
        post_action_prompt=lambda: None,
    )
    assert replies[0] == "\uc774\ubbf8 \uc9c4\ud589 \uc911"
