# -*- coding: utf-8 -*-
"""WorkflowEngine scenarios with in-memory DB and mock Slack client."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.db.models import Base, Channel
from app.db.repository import ChannelRepository, WorkflowRepository
from app.integrations.google_calendar import CalendarCreateResult

from app.schedule.spec import ScheduleSpec, ScheduleType
from app.workflow.engine import WorkflowEngine
from app.workflow.states import WorkflowState

CHANNEL = "C_ENGINE_TEST"


@pytest.fixture()
def session_factory(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        ch = Channel(
            team_id="T1",
            channel_id=CHANNEL,
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
        session.add(ch)
        session.commit()
    return factory


@pytest.fixture()
def slack_client():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "111.222"}
    client.conversations_members.return_value = {"members": ["U1", "U2", "U0BOT"]}
    client.users_info.side_effect = lambda user: {
        "user": {
            "id": user,
            "is_bot": user == "U0BOT",
            "deleted": False,
            "profile": {"display_name": f"name-{user}"},
            "real_name": f"real-{user}",
            "name": user.lower(),
        }
    }
    return client


@pytest.fixture()
def engine(session_factory, slack_client):
    return WorkflowEngine(session_factory, slack_client)


def _first_poll_date(slack_client) -> str:
    blocks = slack_client.chat_postMessage.call_args.kwargs["blocks"]
    for block in blocks:
        for element in block.get("elements", []):
            if element.get("action_id", "").startswith("poll_vote_"):
                return element["value"].split(":", 1)[1]
    raise AssertionError("poll button not found")


def test_start_no_schedule(session_factory, slack_client):
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.schedule_json = None
        session.commit()
    eng = WorkflowEngine(session_factory, slack_client)
    assert eng.start_channel_run(CHANNEL, replace=True) == m.MSG_NO_SCHEDULE


def test_start_disabled(session_factory, slack_client):
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.enabled = False
        session.commit()
    eng = WorkflowEngine(session_factory, slack_client)
    assert eng.start_channel_run(CHANNEL, replace=True) == m.MSG_CHANNEL_DISABLED


def test_start_poll_already_open(engine, session_factory, slack_client):
    assert engine.start_channel_run(CHANNEL, replace=True) is None
    err = engine.start_channel_run(CHANNEL, replace=False)
    assert err == m.MSG_POLL_ALREADY_OPEN


def test_start_second_poll_blocked_without_replace(engine, session_factory):
    assert engine.start_channel_run(CHANNEL, replace=True) is None
    assert engine.start_channel_run(CHANNEL, replace=False) == m.MSG_POLL_ALREADY_OPEN


def test_start_replace_allows_second_poll(engine, session_factory):
    assert engine.start_channel_run(CHANNEL, replace=True) is None
    assert engine.start_channel_run(CHANNEL, replace=True) is None


def test_start_poll_lists_vote_targets(engine, slack_client):
    slack_client.users_info.side_effect = lambda user: {
        "user": {
            "id": user,
            "is_bot": user == "U0BOT",
            "deleted": False,
            "profile": {"display_name": f"name-{user}"},
            "real_name": f"real-{user}",
            "name": user.lower(),
        }
    }

    assert engine.start_channel_run(CHANNEL, replace=True) is None

    intro = slack_client.chat_postMessage.call_args.kwargs["blocks"][0]["text"]["text"]
    assert "투표 대상: 2명" in intro
    assert "<@U1>, <@U2>" in intro
    assert "U0BOT" not in intro


def test_start_poll_uses_configured_targets_and_defaults_new_members(engine, session_factory, slack_client):
    slack_client.conversations_members.return_value = {"members": ["U1", "U2", "U3", "U0BOT"]}
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.poll_target_ids_json = json.dumps(["U1"])
        ch.channel_member_ids_json = json.dumps(["U1", "U2"])
        session.commit()

    assert engine.start_channel_run(CHANNEL, replace=True) is None

    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        assert json.loads(run.target_member_ids_json) == ["U1", "U3"]

    intro = slack_client.chat_postMessage.call_args.kwargs["blocks"][0]["text"]["text"]
    assert "<@U1>" in intro
    assert "<@U3>" in intro
    assert "<@U2>" not in intro


def test_close_poll_assigns_booking_from_poll_target_snapshot(
    engine, session_factory, slack_client, monkeypatch
):
    slack_client.conversations_members.return_value = {"members": ["U1", "U2", "U3", "U4", "U0BOT"]}
    slack_client.users_info.side_effect = lambda user: {
        "user": {
            "id": user,
            "is_bot": user == "U0BOT",
            "deleted": False,
            "profile": {"display_name": f"name-{user}"},
            "real_name": f"real-{user}",
            "name": user.lower(),
        }
    }
    assert engine.start_channel_run(CHANNEL, replace=True) is None
    vote_date = _first_poll_date(slack_client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        run_id = run.id
        assert json.loads(run.target_member_ids_json) == ["U1", "U2", "U3", "U4"]
        WorkflowRepository(session).toggle_vote(run.id, "U1", vote_date)

    slack_client.conversations_members.return_value = {"members": ["U5", "U6"]}
    chosen_pools = []
    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, _members: (["a@example.com"], []),
    )

    def choose(pool):
        chosen_pools.append(list(pool))
        return pool[-1]

    monkeypatch.setattr("app.workflow.engine.random.choice", choose)

    engine.close_poll(run_id)

    assert chosen_pools[-1] == ["U1", "U2", "U3", "U4"]
    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.assignee_user_id == "U4"


def test_close_poll_excludes_previous_assignee_from_target_snapshot(
    engine, session_factory, slack_client, monkeypatch
):
    slack_client.conversations_members.return_value = {"members": ["U1", "U2", "U3", "U4"]}
    assert engine.start_channel_run(CHANNEL, replace=True) is None
    vote_date = _first_poll_date(slack_client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        WorkflowRepository(session).toggle_vote(run.id, "U2", vote_date)
        WorkflowRepository(session).record_assignee(ch.id, "U2")
        run_id = run.id

    chosen_pools = []
    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, _members: (["a@example.com"], []),
    )

    def choose(pool):
        chosen_pools.append(list(pool))
        return pool[0]

    monkeypatch.setattr("app.workflow.engine.random.choice", choose)

    engine.close_poll(run_id)

    assert chosen_pools[-1] == ["U1", "U3", "U4"]


def test_poll_vote_add_remove(engine, session_factory):
    engine.start_channel_run(CHANNEL, replace=True)
    vote_date = _first_poll_date(engine.client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        run_id = run.id
    msg = engine.on_poll_vote(run_id, "U_VOTER", vote_date, CHANNEL)
    assert msg == m.MSG_POLL_VOTE_ADDED.format(date=vote_date)
    msg2 = engine.on_poll_vote(run_id, "U_VOTER", vote_date, CHANNEL)
    assert msg2 == m.MSG_POLL_VOTE_REMOVED.format(date=vote_date)


def test_poll_vote_updates_original_message_with_unavailable_voters(engine, session_factory):
    engine.start_channel_run(CHANNEL, replace=True)
    vote_date = _first_poll_date(engine.client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        run_id = run.id

    msg = engine.on_poll_vote(run_id, "U1", vote_date, CHANNEL)

    assert msg == m.MSG_POLL_VOTE_ADDED.format(date=vote_date)
    engine.client.chat_update.assert_called_once()
    updated = engine.client.chat_update.call_args.kwargs
    assert updated["channel"] == CHANNEL
    assert updated["ts"] == "111.222"
    text = "\n".join(
        block.get("text", {}).get("text", "")
        for block in updated["blocks"]
        if block.get("type") == "section"
    )
    assert vote_date in text
    assert "<@U1>" in text


def test_poll_vote_rejects_date_not_in_poll_options(engine, session_factory):
    engine.start_channel_run(CHANNEL, replace=True)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        run_id = run.id

    msg = engine.on_poll_vote(run_id, "U_VOTER", "1900-01-01", CHANNEL)

    assert msg == "투표 후보에 없는 날짜입니다. 최신 투표 메시지의 날짜 버튼을 눌러 주세요."
    with session_factory() as session:
        votes = WorkflowRepository(session).votes_by_user(run_id)
    assert votes == {}


def test_poll_vote_closed(engine, session_factory):
    engine.start_channel_run(CHANNEL, replace=True)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        WorkflowRepository(session).update_run(run, state=WorkflowState.DONE)
        run_id = run.id
    assert engine.on_poll_vote(run_id, "U1", "2099-06-15", CHANNEL) == m.MSG_POLL_CLOSED


def test_close_poll_no_votes_picks_zero_unavailable_date(
    engine, session_factory, slack_client, monkeypatch
):
    engine.start_channel_run(CHANNEL, replace=True)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        run_id = run.id
    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, _members: (["a@example.com"], []),
    )
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda pool: pool[0])

    engine.close_poll(run_id)

    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.BOOKING_ASSIGNED
        assert json.loads(run.winning_option_json)["date"] is not None
    texts = [c.kwargs.get("text", "") for c in slack_client.chat_postMessage.call_args_list]
    assert m.MSG_NO_VOTES_SKIP not in texts


def test_close_poll_records_date_selection_audit(engine, session_factory, monkeypatch):
    engine.start_channel_run(CHANNEL, replace=True)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        run_id = run.id

    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, _members: (["a@example.com"], []),
    )
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda pool: pool[0])

    engine.close_poll(run_id)

    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        audit = json.loads(run.selection_audit_json)

    date_audit = audit["date"]
    assert audit["schema_version"] == 1
    assert date_audit["poll_semantics"] == "unavailable"
    assert date_audit["candidate_pool"]
    assert date_audit["selection_pool"] == date_audit["candidate_pool"]
    assert date_audit["selected"] == date_audit["candidate_pool"][0]
    assert date_audit["counts"] == {iso: 0 for iso in date_audit["candidate_pool"]}


def test_close_poll_records_assignee_selection_audit(
    engine, session_factory, slack_client, monkeypatch
):
    slack_client.conversations_members.return_value = {"members": ["U1", "U2", "U3"]}
    engine.start_channel_run(CHANNEL, replace=True)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        WorkflowRepository(session).record_assignee(ch.id, "U2")
        run_id = run.id

    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, _members: (["a@example.com"], []),
    )

    def choose(pool):
        return pool[-1]

    monkeypatch.setattr("app.workflow.engine.random.choice", choose)

    engine.close_poll(run_id)

    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        audit = json.loads(run.selection_audit_json)

    assignee_audit = audit["assignee"]
    assert audit["schema_version"] == 1
    assert assignee_audit["candidate_pool"] == ["U1", "U3"]
    assert assignee_audit["previous_assignee"] == "U2"
    assert assignee_audit["selected"] == "U3"


def test_close_poll_with_votes(engine, session_factory, slack_client, monkeypatch):
    engine.start_channel_run(CHANNEL, replace=True)
    vote_date = _first_poll_date(slack_client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        WorkflowRepository(session).toggle_vote(run.id, "U1", vote_date)
        WorkflowRepository(session).toggle_vote(run.id, "U2", vote_date)
        run_id = run.id
    monkeypatch.setattr(
        "app.workflow.engine.list_human_member_ids",
        lambda _client, _ch: ["U1", "U2"],
    )
    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, _members: (["a@example.com"], []),
    )
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda pool: pool[0])
    engine.close_poll(run_id)
    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.BOOKING_ASSIGNED
        assert run.assignee_user_id == "U1"
    posts = [c.kwargs for c in slack_client.chat_postMessage.call_args_list]
    dm_texts = [p.get("text", "") for p in posts if p.get("channel") == "U1"]
    public_texts = [p.get("text", "") for p in posts if p.get("channel") == CHANNEL]
    assert any("calendar.google.com" in text for text in dm_texts)
    assert not any("calendar.google.com" in text for text in public_texts)


def test_close_legacy_poll_without_semantics_uses_available_vote_tally(
    engine, session_factory, monkeypatch
):
    engine.start_channel_run(CHANNEL, replace=True)
    vote_date = _first_poll_date(engine.client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        WorkflowRepository(session).update_run(run, poll_semantics=None)
        WorkflowRepository(session).toggle_vote(run.id, "U1", vote_date)
        run_id = run.id
    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, _members: (["a@example.com"], []),
    )
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda pool: pool[0])

    engine.close_poll(run_id)

    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.BOOKING_ASSIGNED
        assert json.loads(run.winning_option_json)["date"] == vote_date


def test_close_poll_uses_independent_calendar_invite_settings(
    session_factory, slack_client, monkeypatch
):
    calendar_client = MagicMock()
    calendar_client.create_event.return_value = CalendarCreateResult(
        ok=True,
        html_link="https://calendar.google.com/event?eid=direct",
    )
    eng = WorkflowEngine(session_factory, slack_client, calendar_client=calendar_client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.poll_target_ids_json = json.dumps(["U1", "U2"])
        ch.calendar_invitees_json = json.dumps(
            [
                {"kind": "slack", "role": "required", "value": "U2"},
                {"kind": "email", "role": "optional", "value": "guest@example.com"},
                {"kind": "slack", "role": "excluded", "value": "U1"},
            ]
        )
        ch.channel_member_ids_json = json.dumps(["U1", "U2"])
        session.commit()

    assert eng.start_channel_run(CHANNEL, replace=True) is None
    vote_date = _first_poll_date(slack_client)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        WorkflowRepository(session).toggle_vote(run.id, "U1", vote_date)
        run_id = run.id

    monkeypatch.setattr(
        "app.workflow.engine.collect_attendee_emails",
        lambda _session, _client, members: ([f"{uid.lower()}@example.com" for uid in members], []),
    )
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda pool: pool[0])

    eng.close_poll(run_id)

    payload = calendar_client.create_event.call_args.args[0]
    assert payload["attendees"] == [
        {"email": "u2@example.com", "optional": False},
        {"email": "guest@example.com", "optional": True},
    ]
    dm_texts = [
        c.kwargs.get("text", "")
        for c in slack_client.chat_postMessage.call_args_list
        if c.kwargs.get("channel") == "U1"
    ]
    assert any("eid=direct" in text for text in dm_texts)


def test_close_poll_ignores_dates_not_in_poll_options(engine, session_factory, slack_client):
    engine.start_channel_run(CHANNEL, replace=True)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        WorkflowRepository(session).toggle_vote(run.id, "U1", "1900-01-01")
        run_id = run.id

    engine.close_poll(run_id)

    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.BOOKING_ASSIGNED
        winner = json.loads(run.winning_option_json)["date"]
        assert winner != "1900-01-01"
    texts = [c.kwargs.get("text", "") for c in slack_client.chat_postMessage.call_args_list]
    assert m.MSG_NO_VOTES_SKIP not in texts
    assert not any("1900-01-01" in text for text in texts)


def test_cancel_current_run_marks_open_run_done_and_posts_thread(engine, session_factory, slack_client):
    engine.start_channel_run(CHANNEL, replace=True)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run_id = WorkflowRepository(session).get_open_run(ch.id).id

    msg = engine.cancel_current_run(CHANNEL)

    assert msg == m.MSG_RUN_CANCELLED
    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.DONE
    texts = [c.kwargs.get("text", "") for c in slack_client.chat_postMessage.call_args_list]
    assert m.MSG_RUN_CANCELLED in texts


def test_cancel_current_run_reports_when_no_active_run(engine):
    assert engine.cancel_current_run(CHANNEL) == m.MSG_NO_ACTIVE_RUN
