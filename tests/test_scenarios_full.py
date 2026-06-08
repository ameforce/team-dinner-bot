# -*- coding: utf-8 -*-
"""Full scenario matrix from docs/TEST_PLAN_FULL.md (L1 coverage)."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app import messages as m
from app.handlers.intent import dispatch_hoesik_intent
from app.handlers.views import parse_settings_submission
from app.schedule.spec import ScheduleSpec, ScheduleType
from app.slack_invocation import USER_CMD
from app.workflow.engine import WorkflowEngine
from app.workflow.poll import poll_blocks
from app.workflow.states import WorkflowState

CMD = USER_CMD


def _first_poll_date(client) -> str:
    blocks = client.chat_postMessage.call_args.kwargs["blocks"]
    for block in blocks:
        for element in block.get("elements", []):
            if element.get("action_id", "").startswith("poll_vote_"):
                return element["value"].split(":", 1)[1]
    raise AssertionError("poll button not found")


# --- A: Slash command invocation ---


@pytest.mark.parametrize(
    "sub_text,expected_prompt",
    [
        ("", True),
        ("\ub3c4\uc6c0\ub9d0", False),
        ("\uc0c1\ud0dc", True),
        ("\uc77c\uc815", True),
        ("random", True),
    ],
    ids=["A3-empty", "A5-help", "A7-status", "A7-alt-status", "A14-unknown"],
)
def test_slash_dispatch_scenarios(sub_text: str, expected_prompt: bool, monkeypatch):
    replies: list[str] = []
    prompts: list[bool] = []
    monkeypatch.setattr("app.handlers.intent.format_status", lambda *_args: "status ok")

    dispatch_hoesik_intent(
        sub_text=sub_text,
        channel_id="C1",
        user_id="U1",
        session_factory=MagicMock(),
        engine=MagicMock(),
        job_scheduler=None,
        reply=replies.append,
        open_modal=None,
        post_action_prompt=lambda: prompts.append(True),
    )

    assert bool(prompts) is expected_prompt


def test_a8_unknown_subcommand():
    replies: list[str] = []
    prompts: list[bool] = []

    dispatch_hoesik_intent(
        sub_text="unknown-cmd",
        channel_id="C1",
        user_id="U1",
        session_factory=MagicMock(),
        engine=MagicMock(),
        job_scheduler=None,
        reply=replies.append,
        open_modal=lambda: prompts.append(True),
        post_action_prompt=lambda: prompts.append(True),
    )
    assert prompts  # default -> settings buttons


def test_a11_google_command_explains_dm_calendar_link():
    replies: list[str] = []
    dispatch_hoesik_intent(
        sub_text="\uad6c\uae00",
        channel_id="C1",
        user_id="U1",
        session_factory=MagicMock(),
        engine=MagicMock(),
        job_scheduler=None,
        reply=replies.append,
        open_modal=None,
        post_action_prompt=None,
    )
    assert "DM" in replies[0]
    assert "OAuth" not in replies[0]


def test_a12_google_code_no_longer_starts_oauth_flow():
    replies: list[str] = []
    dispatch_hoesik_intent(
        sub_text="\uad6c\uae00\ucf54\ub4dc",
        channel_id="C1",
        user_id="U1",
        session_factory=MagicMock(),
        engine=MagicMock(),
        job_scheduler=None,
        reply=replies.append,
        open_modal=None,
        post_action_prompt=None,
    )
    assert "OAuth" not in replies[0]
    assert "google-code" not in replies[0]


def test_a13_google_code_argument_is_ignored_as_legacy_command():
    replies: list[str] = []
    dispatch_hoesik_intent(
        sub_text="google-code bad-token",
        channel_id="C1",
        user_id="U1",
        session_factory=MagicMock(),
        engine=MagicMock(),
        job_scheduler=None,
        reply=replies.append,
        open_modal=None,
        post_action_prompt=None,
    )
    assert "DM" in replies[0]
    assert "\uc2e4\ud328" not in replies[0]


def test_a14_no_plain_text_or_mention_invocation_handlers(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base
    from app.handlers import events as ev_mod

    db = tmp_path / "a14.db"
    eng = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)

    class FakeApp:
        def __init__(self):
            self.events: dict[str, object] = {}
            self.messages: list[dict] = []

        def event(self, _name):
            def deco(fn):
                self.events[_name] = fn
                return fn

            return deco

        def message(self, **kwargs):
            def deco(fn):
                self.messages.append({"kwargs": kwargs, "fn": fn})
                return fn

            return deco

        def action(self, _action_id):
            def deco(fn):
                return fn

            return deco

    app = FakeApp()
    ev_mod.register_event_handlers(app, factory, engine=MagicMock())

    assert "app_mention" not in app.events
    assert app.messages == []


# --- C: Modal ---


def _monthly_day_view(day: str = "15"):
    return {
        "state": {
            "values": {
                "schedule_type": {
                    "value": {"selected_option": {"value": "MONTHLY_DAY_OF_MONTH"}}
                },
                "day_of_month": {"value": {"value": day}},
                "poll_hour": {"value": {"value": "9"}},
                "poll_duration": {"value": {"value": "36"}},
                "booking_url": {"value": {"value": "https://example.com/book"}},
            }
        }
    }


def test_c2_monthly_day():
    spec, hours, url = parse_settings_submission(_monthly_day_view())
    assert spec.type == ScheduleType.MONTHLY_DAY_OF_MONTH
    assert spec.day == 15
    assert hours == 36
    assert url == "https://example.com/book"


def test_c3_monthly_nth_weekday():
    view = {
        "state": {
            "values": {
                "schedule_type": {
                    "value": {"selected_option": {"value": "MONTHLY_NTH_WEEKDAY"}}
                },
                "weekday": {"value": {"selected_option": {"value": "1"}}},
                "nth": {"value": {"value": "2"}},
                "poll_hour": {"value": {"value": "11"}},
                "poll_duration": {"value": {"value": "48"}},
            }
        }
    }
    spec, hours, url = parse_settings_submission(view)
    assert spec.type == ScheduleType.MONTHLY_NTH_WEEKDAY
    assert spec.weekday == 1
    assert spec.nth == 2


# --- D: Workflow (extends test_engine) ---


@pytest.fixture()
def engine_ctx(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, Channel

    db = tmp_path / "scenarios.db"
    eng = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    ch_id = "C_SCEN"
    with factory() as session:
        session.add(
            Channel(
                team_id="T1",
                channel_id=ch_id,
                enabled=True,
                schedule_json=json.dumps(
                    ScheduleSpec(
                        type=ScheduleType.WEEKLY_WEEKDAY,
                        weekday=1,
                        hour=10,
                        minute=0,
                    ).model_dump(mode="json")
                ),
                poll_duration_hours=48,
                tz="Asia/Seoul",
            )
        )
        session.commit()
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1.1"}
    client.conversations_members.return_value = {"members": ["U1", "U2", "UBOT"]}
    client.users_info.side_effect = lambda user: {
        "user": {
            "id": user,
            "is_bot": user == "UBOT",
            "deleted": False,
            "profile": {"display_name": user},
            "real_name": user,
            "name": user.lower(),
        }
    }
    return factory, WorkflowEngine(factory, client), client, ch_id


def test_d6_close_poll_no_votes(engine_ctx):
    factory, engine, client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    with factory() as session:
        from app.db.repository import ChannelRepository, WorkflowRepository

        row = ChannelRepository(session).get_by_channel_id(ch)
        run_id = WorkflowRepository(session).get_open_run(row.id).id
    engine.close_poll(run_id)
    with factory() as session:
        from app.db.repository import WorkflowRepository

        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.BOOKING_ASSIGNED
        assert run.winning_option_json
    texts = [c.kwargs.get("text", "") for c in client.chat_postMessage.call_args_list]
    assert m.MSG_NO_VOTES_SKIP not in texts


def test_d7_close_poll_with_votes_and_booking(engine_ctx, monkeypatch):
    factory, engine, client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    vote_date = _first_poll_date(client)
    with factory() as session:
        from app.db.repository import ChannelRepository, WorkflowRepository

        row = ChannelRepository(session).get_by_channel_id(ch)
        run = WorkflowRepository(session).get_open_run(row.id)
        WorkflowRepository(session).toggle_vote(run.id, "U1", vote_date)
        run_id = run.id
    monkeypatch.setattr(
        "app.workflow.engine.list_human_member_ids",
        lambda _c, _ch: ["U1", "U2"],
    )
    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda *_a: ([], []),
    )
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda pool: pool[0])
    engine.close_poll(run_id)
    with factory() as session:
        from app.db.repository import WorkflowRepository

        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.BOOKING_ASSIGNED
        assert run.assignee_user_id == "U1"


def test_d8_booking_done(engine_ctx):
    factory, engine, client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    with factory() as session:
        from app.db.repository import ChannelRepository, WorkflowRepository

        row = ChannelRepository(session).get_by_channel_id(ch)
        run = WorkflowRepository(session).get_open_run(row.id)
        WorkflowRepository(session).update_run(
            run,
            state=WorkflowState.BOOKING_ASSIGNED,
            assignee_user_id="U1",
            winning_option_json='{"date":"2099-06-20","counts":{"2099-06-20":1}}',
        )
        run_id = run.id
    assert engine.on_booking_done(run_id, "U2") == m.MSG_ONLY_ASSIGNEE
    assert engine.on_booking_done(run_id, "U1") == m.MSG_BOOKING_DONE_OK
    assert engine.on_booking_done(run_id, "U1") == m.MSG_ALREADY_DONE


def test_msg_poll_closed_uses_ma_not_mal():
    """Regression: typo was 맄감 (U+B9C4) instead of 마감 (U+B9C8)."""
    assert "\ub9c4\uac10" not in m.MSG_POLL_CLOSED
    assert m.MSG_POLL_CLOSED == "\ub9c8\uac10\ub41c \ud22c\ud45c\uc785\ub2c8\ub2e4."


def test_poll_intro_deadline_uses_ma_not_mal():
    from datetime import date
    from zoneinfo import ZoneInfo

    blocks = poll_blocks(
        1,
        [date(2099, 6, 15)],
        datetime(2099, 6, 20, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    intro = blocks[0]["text"]["text"]
    assert "\ub9c4\uac10" not in intro
    assert "\ub9c8\uac10:" in intro


def test_d9_booking_done_rejected_while_poll_open(engine_ctx):
    factory, engine, client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    with factory() as session:
        from app.db.repository import ChannelRepository, WorkflowRepository

        row = ChannelRepository(session).get_by_channel_id(ch)
        run_id = WorkflowRepository(session).get_open_run(row.id).id
    assert engine.on_booking_done(run_id, "U1") == m.MSG_BOOKING_NOT_READY
    texts = [c.kwargs.get("text", "") for c in client.chat_postMessage.call_args_list]
    assert not any("\ud750\ub984\uc774 \uc885\ub8cc" in t for t in texts)


def test_d10_force_start_aborts_previous_open_run(engine_ctx):
    factory, engine, client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    with factory() as session:
        from app.db.repository import ChannelRepository, WorkflowRepository

        row = ChannelRepository(session).get_by_channel_id(ch)
        first_id = WorkflowRepository(session).get_open_run(row.id).id
    engine.start_channel_run(ch, replace=True)
    with factory() as session:
        from app.db.repository import WorkflowRepository

        first = WorkflowRepository(session).get_run(first_id)
        second = WorkflowRepository(session).get_open_run(first.channel_id)
        assert first.state == WorkflowState.DONE
        assert second.id > first_id
        assert second.state == WorkflowState.POLL_OPEN
    texts = [c.kwargs.get("text", "") for c in client.chat_postMessage.call_args_list]
    assert not any("\ud750\ub984\uc774 \uc885\ub8cc" in t for t in texts)


def test_d11_poll_open_posts_no_workflow_end_message(engine_ctx):
    factory, engine, client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    texts = [c.kwargs.get("text", "") for c in client.chat_postMessage.call_args_list]
    assert m.MSG_POLL_STARTED in texts
    assert not any("\uc608\uc57d\uc744 \uc644\ub8cc" in t and "\uc885\ub8cc" in t for t in texts)


def test_b4_poll_closed_vote(engine_ctx):
    factory, engine, _client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    with factory() as session:
        from app.db.repository import ChannelRepository, WorkflowRepository

        row = ChannelRepository(session).get_by_channel_id(ch)
        run = WorkflowRepository(session).get_open_run(row.id)
        WorkflowRepository(session).update_run(run, state=WorkflowState.DONE)
        run_id = run.id
    result = engine.on_poll_vote(run_id, "U1", "2099-06-15", ch)
    assert result.needs_feedback is True
    assert result.feedback_text == m.MSG_POLL_CLOSED


def test_poll_blocks_max_five_per_row():
    from datetime import date

    dates = [date(2026, 6, i) for i in range(1, 12)]
    blocks = poll_blocks(99, dates, datetime(2026, 6, 1, 12, 0))
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks) == 3
    assert len(action_blocks[0]["elements"]) == 5
    assert len(action_blocks[1]["elements"]) == 5
    assert len(action_blocks[2]["elements"]) == 1


def test_d12_second_poll_without_replace_blocked(engine_ctx):
    factory, engine, _client, ch = engine_ctx
    engine.start_channel_run(ch, replace=True)
    assert engine.start_channel_run(ch, replace=False) == m.MSG_POLL_ALREADY_OPEN


def test_poll_uses_business_days_rest_of_month():
    from datetime import date
    from zoneinfo import ZoneInfo

    from app.workflow.dates import business_days_rest_of_month

    anchor = datetime(2026, 5, 20, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    days = business_days_rest_of_month(after=anchor, tz_name="Asia/Seoul")
    assert days[0] == date(2026, 5, 20)
    assert days[-1] == date(2026, 5, 29)
    assert all(d.weekday() < 5 for d in days)
    assert date(2026, 5, 24) not in days
    assert len(days) == 8


# --- E: Member events (handler logic) ---


def test_e1_member_joined_bot_only(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base
    from app.db.repository import ChannelRepository
    from app.handlers import events as ev_mod

    db = tmp_path / "e.db"
    eng = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)

    posted: list[dict] = []
    client = MagicMock()
    client.auth_test.return_value = {"user_id": "UBOT", "team_id": "T1"}
    client.chat_postMessage.side_effect = lambda **kw: posted.append(kw) or {"ok": True}

    handlers: dict = {}

    class App:
        def event(self, name):
            def wrap(fn):
                handlers[name] = fn
                return fn

            return wrap

        def action(self, _id):
            def wrap(fn):
                return fn

            return wrap

        def message(self, **kwargs):
            def wrap(fn):
                return fn

            return wrap

    ev_mod.register_event_handlers(App(), factory, engine=MagicMock())
    handlers["member_joined_channel"](
        {"user": "UBOT", "channel": "C_JOIN", "team": "T1"},
        client,
        MagicMock(),
    )
    with factory() as session:
        ch = ChannelRepository(session).get_by_channel_id("C_JOIN")
        assert ch is not None
    assert posted


def test_e2_member_left_disables(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import Base, Channel
    from app.db.repository import ChannelRepository
    from app.handlers import events as ev_mod

    db = tmp_path / "e2.db"
    eng = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng)
    with factory() as session:
        session.add(
            Channel(
                team_id="T1",
                channel_id="C_LEFT",
                enabled=True,
                schedule_json="{}",
                poll_duration_hours=48,
                tz="Asia/Seoul",
            )
        )
        session.commit()

    client = MagicMock()
    client.auth_test.return_value = {"user_id": "UBOT"}
    sched = MagicMock()
    handlers: dict = {}

    class App:
        def event(self, name):
            def wrap(fn):
                handlers[name] = fn
                return fn

            return wrap

        def action(self, _id):
            def wrap(fn):
                return fn

            return wrap

        def message(self, **kwargs):
            def wrap(fn):
                return fn

            return wrap

    ev_mod.register_event_handlers(App(), factory, engine=MagicMock(), job_scheduler=sched)
    handlers["member_left_channel"]({"user": "UBOT", "channel": "C_LEFT"}, client)
    sched.schedule_channel.assert_called_once_with("C_LEFT")
    with factory() as session:
        ch = ChannelRepository(session).get_by_channel_id("C_LEFT")
        assert ch.enabled is False


# --- F: Scheduler ---


def test_f1_schedule_channel_adds_job(engine_ctx):
    factory, engine, _client, ch = engine_ctx
    sched = __import__("app.scheduler.runner", fromlist=["JobScheduler"]).JobScheduler(
        factory, engine
    )
    sched.scheduler = MagicMock()
    sched.scheduler.get_jobs.return_value = []
    sched.schedule_channel(ch)
    sched.scheduler.add_job.assert_called_once()


def test_f1_schedule_channel_skips_when_automatic_execution_off(engine_ctx):
    factory, engine, _client, ch = engine_ctx
    with factory() as session:
        from app.db.repository import ChannelRepository

        row = ChannelRepository(session).get_by_channel_id(ch)
        row.automatic_execution_enabled = False
        session.commit()
    sched = __import__("app.scheduler.runner", fromlist=["JobScheduler"]).JobScheduler(
        factory, engine
    )
    sched.scheduler = MagicMock()
    sched.scheduler.get_jobs.return_value = []

    sched.schedule_channel(ch)

    sched.scheduler.add_job.assert_not_called()


def test_f2_schedule_poll_close(engine_ctx):
    factory, engine, _client, ch = engine_ctx
    sched = __import__("app.scheduler.runner", fromlist=["JobScheduler"]).JobScheduler(
        factory, engine
    )
    sched.scheduler = MagicMock()
    sched.scheduler.get_jobs.return_value = []
    deadline = datetime(2099, 1, 1, 12, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Seoul"))
    sched.schedule_poll_close(42, deadline)
    sched.scheduler.add_job.assert_called_once()
    assert sched.scheduler.add_job.call_args.kwargs["kwargs"]["run_id"] == 42
