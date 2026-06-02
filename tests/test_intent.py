# -*- coding: utf-8 -*-
"""Natural-language invocation parsing and dispatch branches."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.handlers.intent import (
    dispatch_hoesik_intent,
    help_text,
    normalize_invocation_text,
)
from app.slack_invocation import USER_CMD

CMD = USER_CMD  # \ud68c\uc2dd


@pytest.mark.parametrize(
    "raw,expected",
    [
        (CMD, ""),
        (f"{CMD} status", "status"),
        (f"/{CMD}", ""),
        (f" /{CMD} help", "help"),
        (f"<@U0BOT> {CMD}", ""),
        (f"<@U0BOT> {CMD} \uc0c1\ud0dc", "\uc0c1\ud0dc"),
        (f"<@U0BOT> {CMD} run-now", "run-now"),
        ("hello", None),
        ("", None),
    ],
)
def test_normalize_invocation_text(raw: str, expected: str | None):
    assert normalize_invocation_text(raw) == expected


def test_help_text_contains_commands():
    text = help_text()
    assert USER_CMD in text
    assert "status" in text or "\uc0c1\ud0dc" in text
    assert "OAuth" not in text
    assert "google-code" not in text


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


def test_message_matcher_skips_mention_text():
    from app.handlers.intent import register_natural_language_handlers

    items: list = []

    class FakeApp:
        def event(self, _):
            def d(fn):
                return fn

            return d

        def message(self, **kw):
            def d(fn):
                items.append(kw["matchers"][0])
                return fn

            return d

    register_natural_language_handlers(FakeApp(), MagicMock(), MagicMock())
    matcher = items[0]
    assert matcher({"text": f"<@UBOT> {CMD}", "user": "U1"}) is False
    assert matcher({"text": CMD, "user": "U1"}) is True


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
