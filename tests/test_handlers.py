# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.db.models import Base, Channel
from app.db.repository import ChannelRepository
from app.handlers.actions import register_action_handlers
from app.handlers.commands import register_command_handlers
from app.handlers.events import register_event_handlers
from app.schedule.spec import ScheduleSpec, ScheduleType


CHANNEL = "C_HANDLER_TEST"


class FakeApp:
    def __init__(self):
        self.actions: dict[str, object] = {}
        self.events: dict[str, object] = {}
        self.views: dict[str, object] = {}
        self.messages: list[dict] = []

    def action(self, action_id):
        def wrap(fn):
            self.actions[action_id] = fn
            return fn

        return wrap

    def event(self, name):
        def wrap(fn):
            self.events[name] = fn
            return fn

        return wrap

    def view(self, callback_id):
        def wrap(fn):
            self.views[callback_id] = fn
            return fn

        return wrap

    def message(self, **kwargs):
        def wrap(fn):
            self.messages.append({"kwargs": kwargs, "fn": fn})
            return fn

        return wrap


@pytest.fixture()
def session_factory(tmp_path):
    db = tmp_path / "handlers.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    spec = ScheduleSpec(type=ScheduleType.WEEKLY_WEEKDAY, weekday=1, hour=10, minute=0)
    with factory() as session:
        session.add(
            Channel(
                team_id="T1",
                channel_id=CHANNEL,
                enabled=True,
                schedule_json=spec.model_dump_json(),
                poll_duration_hours=48,
                tz="Asia/Seoul",
                booking_url_template="https://example.com/book",
            )
        )
        session.commit()
    return factory


def _settings_view(channel_id: str = CHANNEL) -> dict:
    return {
        "private_metadata": channel_id,
        "state": {
            "values": {
                "schedule_type": {
                    "value": {"selected_option": {"value": "MONTHLY_DAY_OF_MONTH"}}
                },
                "day_of_month": {"value": {"value": "15"}},
                "poll_hour": {"value": {"value": "11"}},
                "poll_duration": {"value": {"value": "36"}},
                "booking_url": {"value": {"value": "https://example.com/new-book"}},
                "poll_targets": {"value": {"selected_users": ["U1", "U2"]}},
                "calendar_required": {"value": {"selected_users": ["U1"]}},
                "calendar_required_emails": {"value": {"value": "guest@example.com"}},
                "calendar_optional": {"value": {"selected_users": []}},
                "calendar_excluded": {"value": {"selected_users": ["U2"]}},
            }
        },
    }


def _block(view: dict, block_id: str) -> dict:
    return next(block for block in view["blocks"] if block.get("block_id") == block_id)


def test_open_settings_action_opens_prefilled_modal(session_factory):
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.poll_target_ids_json = json.dumps(["U1", "U2"])
        ch.calendar_invitees_json = json.dumps(
            [
                {"kind": "slack", "role": "required", "value": "U1"},
                {"kind": "email", "role": "optional", "value": "guest@example.com"},
                {"kind": "slack", "role": "excluded", "value": "U2"},
            ]
        )
        session.commit()
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["open_settings"](
        ack,
        {"channel": {"id": CHANNEL}, "trigger_id": "TRIGGER1"},
        client,
    )

    ack.assert_called_once()
    opened = client.views_open.call_args.kwargs
    assert opened["trigger_id"] == "TRIGGER1"
    assert "불러오는 중" in opened["view"]["blocks"][0]["text"]["text"]
    view = client.views_update.call_args.kwargs["view"]
    assert view["private_metadata"] == CHANNEL
    assert view["callback_id"] == "settings_submit"
    assert _block(view, "schedule_type")["element"]["initial_option"]["value"] == "WEEKLY_WEEKDAY"
    assert _block(view, "weekday")["element"]["initial_option"]["value"] == "1"
    assert _block(view, "poll_hour")["element"]["initial_value"] == "10"
    assert _block(view, "poll_duration")["element"]["initial_value"] == "48"
    assert _block(view, "booking_url")["element"]["initial_value"] == "https://example.com/book"
    assert _block(view, "poll_targets")["element"]["initial_users"] == ["U1", "U2"]
    assert _block(view, "calendar_required")["element"]["initial_users"] == ["U1"]
    assert _block(view, "calendar_optional_emails")["element"]["initial_value"] == "guest@example.com"
    assert "initial_users" not in _block(view, "calendar_optional")["element"]
    assert _block(view, "calendar_excluded")["element"]["initial_users"] == ["U2"]


def test_open_settings_uses_trigger_before_member_lookup(session_factory, monkeypatch):
    events = []
    app = FakeApp()
    client = MagicMock()
    client.views_open.side_effect = lambda **_kwargs: events.append("views_open") or {
        "view": {"id": "V_SETTINGS"}
    }

    def fake_member_lookup(_client, _channel_id):
        events.append("member_lookup")
        return ["U1"]

    monkeypatch.setattr("app.handlers.events.list_human_member_ids", fake_member_lookup)
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["open_settings"](
        ack,
        {"channel": {"id": CHANNEL}, "trigger_id": "TRIGGER1"},
        client,
    )

    assert events == ["views_open", "member_lookup"]
    client.views_update.assert_called_once()
    assert client.views_update.call_args.kwargs["view_id"] == "V_SETTINGS"


def test_open_settings_defaults_unconfigured_participants_to_channel_members(session_factory):
    app = FakeApp()
    client = MagicMock()
    client.views_open.return_value = {"view": {"id": "V_SETTINGS"}}
    client.conversations_members.return_value = {"members": ["U1", "U2"]}
    client.users_info.side_effect = lambda user: {
        "user": {
            "id": user,
            "is_bot": False,
            "deleted": False,
            "profile": {"display_name": user},
        }
    }
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["open_settings"](
        ack,
        {"channel": {"id": CHANNEL}, "trigger_id": "TRIGGER1"},
        client,
    )

    view = client.views_update.call_args.kwargs["view"]
    assert _block(view, "poll_targets")["element"]["initial_users"] == ["U1", "U2"]
    assert _block(view, "calendar_required")["element"]["initial_users"] == ["U1", "U2"]
    assert "initial_users" not in _block(view, "calendar_optional")["element"]
    assert "initial_users" not in _block(view, "calendar_excluded")["element"]


def test_show_status_action_posts_ephemeral_status(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["show_status"](
        ack,
        {"channel": {"id": CHANNEL}, "user": {"id": "U1"}},
        client,
    )

    ack.assert_called_once()
    client.chat_postEphemeral.assert_called_once()
    kwargs = client.chat_postEphemeral.call_args.kwargs
    assert kwargs["channel"] == CHANNEL
    assert kwargs["user"] == "U1"
    assert m.MSG_STATUS_HEADER in kwargs["text"]
    assert any(
        el.get("action_id") == "open_settings"
        for block in kwargs["blocks"]
        if block["type"] == "actions"
        for el in block["elements"]
    )


def test_cancel_current_run_action_calls_engine_and_posts_ephemeral(session_factory, monkeypatch):
    monkeypatch.setattr("app.handlers.events.settings.admin_user_ids", "")
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    engine = MagicMock()
    engine.cancel_current_run.return_value = "cancel ok"
    register_event_handlers(app, session_factory, engine=engine)

    app.actions["cancel_current_run"](
        ack,
        {"channel": {"id": CHANNEL}, "user": {"id": "U1"}},
        client,
    )

    ack.assert_called_once()
    engine.cancel_current_run.assert_called_once_with(CHANNEL)
    client.chat_postEphemeral.assert_called_once_with(
        channel=CHANNEL,
        user="U1",
        text="cancel ok",
    )


def test_start_poll_now_action_calls_engine_and_posts_ephemeral(session_factory, monkeypatch):
    monkeypatch.setattr("app.handlers.events.settings.admin_user_ids", "")
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    engine = MagicMock()
    engine.start_channel_run.return_value = None
    register_event_handlers(app, session_factory, engine=engine)

    app.actions["start_poll_now"](
        ack,
        {"channel": {"id": CHANNEL}, "user": {"id": "U1"}},
        client,
    )

    ack.assert_called_once()
    engine.start_channel_run.assert_called_once_with(CHANNEL, replace=False)
    client.chat_postEphemeral.assert_called_once()


def test_settings_submit_saves_schedule_and_booking_url(session_factory):
    app = FakeApp()
    client = MagicMock()
    client.conversations_members.return_value = {"members": ["U1", "U2"]}
    client.users_info.side_effect = lambda user: {
        "user": {
            "id": user,
            "is_bot": False,
            "deleted": False,
            "profile": {"display_name": user},
        }
    }
    ack = MagicMock()
    scheduler = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock(), job_scheduler=scheduler)

    app.views["settings_submit"](ack, {}, client, _settings_view())

    ack.assert_called_once()
    scheduler.schedule_channel.assert_called_once_with(CHANNEL)
    client.chat_postMessage.assert_called_once()
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        saved = json.loads(ch.schedule_json)
        assert saved["type"] == "MONTHLY_DAY_OF_MONTH"
        assert saved["day"] == 15
        assert ch.poll_duration_hours == 36
        assert ch.booking_url_template == "https://example.com/new-book"
        assert json.loads(ch.poll_target_ids_json) == ["U1", "U2"]
        assert json.loads(ch.channel_member_ids_json) == ["U1", "U2"]
        assert json.loads(ch.calendar_invitees_json) == [
            {"kind": "slack", "role": "required", "value": "U1"},
            {"kind": "email", "role": "required", "value": "guest@example.com"},
            {"kind": "slack", "role": "excluded", "value": "U2"},
        ]


def test_settings_submit_can_clear_booking_url(session_factory):
    app = FakeApp()
    client = MagicMock()
    client.conversations_members.return_value = {"members": ["U1", "U2"]}
    client.users_info.side_effect = lambda user: {
        "user": {
            "id": user,
            "is_bot": False,
            "deleted": False,
            "profile": {"display_name": user},
        }
    }
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())
    view = _settings_view()
    view["state"]["values"]["booking_url"]["value"]["value"] = ""

    app.views["settings_submit"](ack, {}, client, view)

    ack.assert_called_once()
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        assert ch.booking_url_template is None


def test_settings_submit_invalid_view_notifies_channel(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())

    app.views["settings_submit"](ack, {}, client, {"private_metadata": CHANNEL, "state": {"values": {}}})

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once_with(channel=CHANNEL, text=m.MSG_SETTINGS_INVALID)


def test_settings_submit_malformed_selected_option_notifies_channel(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())
    view = _settings_view()
    view["state"]["values"]["schedule_type"]["value"]["selected_option"] = {"text": "missing value"}

    app.views["settings_submit"](ack, {}, client, view)

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once_with(channel=CHANNEL, text=m.MSG_SETTINGS_INVALID)


def test_settings_submit_malformed_state_notifies_channel(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())

    app.views["settings_submit"](ack, {}, client, {"private_metadata": CHANNEL, "state": {}})

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once_with(channel=CHANNEL, text=m.MSG_SETTINGS_INVALID)


def test_settings_submit_missing_private_metadata_does_not_post(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    scheduler = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock(), job_scheduler=scheduler)

    app.views["settings_submit"](ack, {}, client, _settings_view(channel_id=""))

    ack.assert_called_once()
    client.chat_postMessage.assert_not_called()
    scheduler.schedule_channel.assert_not_called()


def test_settings_submit_missing_channel_without_team_does_not_create_sentinel(tmp_path):
    db = tmp_path / "empty-handlers.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, factory, engine=MagicMock())

    app.views["settings_submit"](ack, {}, client, _settings_view())

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once_with(channel=CHANNEL, text=m.MSG_SETTINGS_INVALID)
    client.conversations_members.assert_not_called()
    with factory() as session:
        assert ChannelRepository(session).get_by_channel_id(CHANNEL) is None


def test_settings_submit_missing_channel_with_blank_team_does_not_create_sentinel(tmp_path):
    db = tmp_path / "blank-team-handlers.db"
    engine = create_engine(f"sqlite:///{db}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, factory, engine=MagicMock())

    app.views["settings_submit"](ack, {"team": {"id": "   "}}, client, _settings_view())

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once_with(channel=CHANNEL, text=m.MSG_SETTINGS_INVALID)
    client.conversations_members.assert_not_called()
    with factory() as session:
        assert ChannelRepository(session).get_by_channel_id(CHANNEL) is None


def test_settings_submit_existing_blank_team_without_team_notifies_channel(session_factory):
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.team_id = ""
        session.commit()
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())

    app.views["settings_submit"](ack, {}, client, _settings_view())

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once_with(channel=CHANNEL, text=m.MSG_SETTINGS_INVALID)
    client.conversations_members.assert_not_called()


def test_settings_submit_existing_blank_team_with_blank_team_notifies_channel(session_factory):
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.team_id = ""
        session.commit()
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())

    app.views["settings_submit"](ack, {"team": {"id": "   "}}, client, _settings_view())

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once_with(channel=CHANNEL, text=m.MSG_SETTINGS_INVALID)
    client.conversations_members.assert_not_called()


def test_upsert_on_bot_join_rejects_blank_team_id(session_factory):
    with session_factory() as session:
        repo = ChannelRepository(session)
        with pytest.raises(ValueError):
            repo.upsert_on_bot_join("", "C_NEW")


def test_upsert_on_bot_join_repairs_blank_team_id(session_factory):
    with session_factory() as session:
        session.add(Channel(team_id="", channel_id="C_BLANK", enabled=False))
        session.commit()
        repo = ChannelRepository(session)

        row = repo.upsert_on_bot_join(" T_REAL ", "C_BLANK")

        assert row.enabled is True
        assert row.team_id == "T_REAL"


def test_poll_vote_action_calls_engine_and_posts_ephemeral():
    app = FakeApp()
    engine = MagicMock()
    engine.on_poll_vote.return_value = "vote ok"
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    action_key = next(key for key in app.actions if hasattr(key, "match"))
    app.actions[action_key](
        ack,
        {
            "actions": [{"value": "42:2099-06-20"}],
            "user": {"id": "U1"},
            "channel": {"id": CHANNEL},
        },
        client,
    )

    ack.assert_called_once()
    engine.on_poll_vote.assert_called_once_with(42, "U1", "2099-06-20", CHANNEL)
    client.chat_postEphemeral.assert_called_once_with(channel=CHANNEL, user="U1", text="vote ok")


def test_poll_vote_action_malformed_value_posts_invalid_and_skips_engine():
    app = FakeApp()
    engine = MagicMock()
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    action_key = next(key for key in app.actions if hasattr(key, "match"))
    app.actions[action_key](
        ack,
        {
            "actions": [{"value": "not-a-run-id"}],
            "user": {"id": "U1"},
            "channel": {"id": CHANNEL},
        },
        client,
    )

    ack.assert_called_once()
    engine.on_poll_vote.assert_not_called()
    client.chat_postEphemeral.assert_called_once_with(
        channel=CHANNEL,
        user="U1",
        text=m.MSG_ACTION_INVALID,
    )


def test_poll_vote_action_missing_action_does_not_raise():
    app = FakeApp()
    engine = MagicMock()
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    action_key = next(key for key in app.actions if hasattr(key, "match"))
    app.actions[action_key](
        ack,
        {"actions": [], "user": {"id": "U1"}, "channel": {"id": CHANNEL}},
        client,
    )

    ack.assert_called_once()
    engine.on_poll_vote.assert_not_called()
    client.chat_postEphemeral.assert_called_once_with(
        channel=CHANNEL,
        user="U1",
        text=m.MSG_ACTION_INVALID,
    )


def test_poll_vote_action_missing_context_skips_engine():
    app = FakeApp()
    engine = MagicMock()
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    action_key = next(key for key in app.actions if hasattr(key, "match"))
    app.actions[action_key](
        ack,
        {"actions": [{"value": "42:2099-06-20"}], "user": {"id": "U1"}},
        client,
    )

    ack.assert_called_once()
    engine.on_poll_vote.assert_not_called()
    client.chat_postEphemeral.assert_not_called()


def test_poll_vote_action_whitespace_context_skips_engine():
    app = FakeApp()
    engine = MagicMock()
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    action_key = next(key for key in app.actions if hasattr(key, "match"))
    app.actions[action_key](
        ack,
        {
            "actions": [{"value": "42:2099-06-20"}],
            "user": {"id": "   "},
            "channel": {"id": "   "},
        },
        client,
    )

    ack.assert_called_once()
    engine.on_poll_vote.assert_not_called()
    client.chat_postEphemeral.assert_not_called()


def test_booking_done_action_calls_engine_and_posts_ephemeral():
    app = FakeApp()
    engine = MagicMock()
    engine.on_booking_done.return_value = "done ok"
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    app.actions["booking_done"](
        ack,
        {
            "actions": [{"value": "42"}],
            "user": {"id": "U1"},
            "channel": {"id": CHANNEL},
        },
        client,
    )

    ack.assert_called_once()
    engine.on_booking_done.assert_called_once_with(42, "U1")
    client.chat_postEphemeral.assert_called_once_with(channel=CHANNEL, user="U1", text="done ok")


def test_booking_done_action_malformed_value_posts_invalid_and_skips_engine():
    app = FakeApp()
    engine = MagicMock()
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    app.actions["booking_done"](
        ack,
        {
            "actions": [{"value": "bad"}],
            "user": {"id": "U1"},
            "channel": {"id": CHANNEL},
        },
        client,
    )

    ack.assert_called_once()
    engine.on_booking_done.assert_not_called()
    client.chat_postEphemeral.assert_called_once_with(
        channel=CHANNEL,
        user="U1",
        text=m.MSG_ACTION_INVALID,
    )


def test_booking_done_action_missing_context_skips_engine():
    app = FakeApp()
    engine = MagicMock()
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    app.actions["booking_done"](
        ack,
        {"actions": [{"value": "42"}], "channel": {"id": CHANNEL}},
        client,
    )

    ack.assert_called_once()
    engine.on_booking_done.assert_not_called()
    client.chat_postEphemeral.assert_not_called()


def test_booking_done_action_whitespace_context_skips_engine():
    app = FakeApp()
    engine = MagicMock()
    client = MagicMock()
    ack = MagicMock()
    register_action_handlers(app, MagicMock(), engine)

    app.actions["booking_done"](
        ack,
        {
            "actions": [{"value": "42"}],
            "user": {"id": "   "},
            "channel": {"id": "   "},
        },
        client,
    )

    ack.assert_called_once()
    engine.on_booking_done.assert_not_called()
    client.chat_postEphemeral.assert_not_called()
