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
from app.handlers.views import encode_settings_metadata
from app.schedule.spec import ScheduleSpec, ScheduleType
from app.workflow.engine import PollVoteResult


CHANNEL = "C_HANDLER_TEST"


class FakeApp:
    def __init__(self):
        self.actions: dict[str, object] = {}
        self.commands: dict[str, object] = {}
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

    def command(self, name):
        def wrap(fn):
            self.commands[name] = fn
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
                "automatic_execution": {
                    "value": {"selected_option": {"value": "on"}}
                },
                "schedule_type": {
                    "value": {"selected_option": {"value": "MONTHLY_DAY_OF_MONTH"}}
                },
                "day_of_month": {"value": {"value": "15"}},
                "month_interval": {"value": {"value": "2"}},
                "poll_hour": {"value": {"value": "11"}},
                "poll_duration": {"value": {"value": "36"}},
                "booking_url": {"value": {"value": "https://example.com/new-book"}},
                "poll_targets": {"value": {"selected_users": ["U1", "U2"]}},
                "calendar_required": {"value": {"selected_users": ["U1"]}},
                "calendar_required_emails": {"value": {"value": "guest@example.com"}},
                "calendar_optional": {"value": {"selected_users": ["U2"]}},
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
    assert _block(view, "legacy_weekly_notice")
    assert _block(view, "schedule_type")["element"]["initial_option"]["value"] == "MONTHLY_DAY_OF_MONTH"
    assert _block(view, "automatic_execution")["element"]["initial_option"]["value"] == "on"
    assert "WEEKLY_WEEKDAY" not in [
        option["value"] for option in _block(view, "schedule_type")["element"]["options"]
    ]
    assert _block(view, "day_of_month")["element"]["initial_value"] == "15"
    assert _block(view, "poll_hour")["element"]["initial_value"] == "10"
    assert _block(view, "poll_duration")["element"]["initial_value"] == "48"
    assert _block(view, "booking_url")["element"]["initial_value"] == "https://example.com/book"
    assert _block(view, "poll_targets")["element"]["initial_users"] == ["U1", "U2"]
    assert _block(view, "calendar_required")["element"]["initial_users"] == ["U1"]
    assert "initial_users" not in _block(view, "calendar_optional")["element"]
    block_ids = {block.get("block_id") for block in view["blocks"]}
    assert "calendar_required_emails" not in block_ids
    assert "calendar_optional_emails" not in block_ids
    assert "calendar_excluded" not in block_ids
    assert "calendar_excluded_emails" not in block_ids


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
    assert not any(block.get("block_id") == "calendar_excluded" for block in view["blocks"])


def test_register_command_handlers_registers_hoesik_slash_command(session_factory):
    app = FakeApp()

    register_command_handlers(app, session_factory, engine=MagicMock())

    assert "/회식" in app.commands


def test_register_event_handlers_does_not_register_text_invocation_handlers(session_factory):
    app = FakeApp()

    register_event_handlers(app, session_factory, engine=MagicMock())

    assert "app_mention" not in app.events
    assert app.messages == []


def test_hoesik_slash_command_posts_action_panel(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())

    app.commands["/회식"](
        ack,
        {"channel_id": CHANNEL, "user_id": "U1", "text": "", "trigger_id": "TRIGGER1"},
        client,
        MagicMock(),
    )

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once()
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == CHANNEL
    assert kwargs["text"] == m.MSG_SETTINGS_PROMPT
    assert any(
        el.get("action_id") == "open_settings"
        for block in kwargs["blocks"]
        if block["type"] == "actions"
        for el in block["elements"]
    )
    client.views_open.assert_not_called()


def test_hoesik_settings_slash_command_opens_settings_modal_directly(
    session_factory, monkeypatch
):
    events = []
    app = FakeApp()
    client = MagicMock()
    client.views_open.side_effect = lambda **_kwargs: events.append("views_open") or {
        "view": {"id": "V_SETTINGS"}
    }

    def fake_member_lookup(_client, _channel_id):
        events.append("member_lookup")
        return ["U1"]

    monkeypatch.setattr("app.handlers.commands.list_human_member_ids", fake_member_lookup)
    ack = MagicMock()
    register_command_handlers(app, session_factory, engine=MagicMock())

    app.commands["/회식"](
        ack,
        {"channel_id": CHANNEL, "user_id": "U1", "text": "설정", "trigger_id": "TRIGGER1"},
        client,
        MagicMock(),
    )

    ack.assert_called_once()
    assert events == ["views_open", "member_lookup"]
    client.chat_postMessage.assert_not_called()
    client.views_open.assert_called_once()
    client.views_update.assert_called_once()
    view = client.views_update.call_args.kwargs["view"]
    assert view["callback_id"] == "settings_submit"
    assert _block(view, "calendar_required")


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
        assert saved["month_interval"] == 2
        assert ch.automatic_execution_enabled is True
        assert ch.poll_duration_hours == 36
        assert ch.booking_url_template == "https://example.com/new-book"
        assert json.loads(ch.poll_target_ids_json) == ["U1", "U2"]
        assert json.loads(ch.channel_member_ids_json) == ["U1", "U2"]
        assert json.loads(ch.calendar_invitees_json) == [
            {"kind": "slack", "role": "required", "value": "U1"},
            {"kind": "slack", "role": "optional", "value": "U2"},
        ]


def test_settings_submit_sets_month_interval_anchor(session_factory, monkeypatch):
    class FixedDateTime:
        @classmethod
        def now(cls, _tz):
            from datetime import datetime

            return datetime(2026, 5, 20, 12, 0, tzinfo=_tz)

    monkeypatch.setattr("app.db.repository.datetime", FixedDateTime)
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
    register_command_handlers(app, session_factory, engine=MagicMock(), job_scheduler=MagicMock())

    app.views["settings_submit"](ack, {}, client, _settings_view())

    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        saved = json.loads(ch.schedule_json)
        assert saved["month_anchor_year"] == 2026
        assert saved["month_anchor_month"] == 5


def test_settings_submit_preserves_month_interval_anchor_when_only_time_changes(
    session_factory,
    monkeypatch,
):
    class LaterDateTime:
        @classmethod
        def now(cls, _tz):
            from datetime import datetime

            return datetime(2026, 6, 1, 12, 0, tzinfo=_tz)

    spec = ScheduleSpec(
        type=ScheduleType.MONTHLY_DAY_OF_MONTH,
        day=15,
        month_interval=2,
        month_anchor_year=2026,
        month_anchor_month=5,
        hour=10,
        minute=0,
    )
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.schedule_json = spec.model_dump_json()
        session.commit()
    monkeypatch.setattr("app.db.repository.datetime", LaterDateTime)
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
    view = _settings_view()
    view["state"]["values"]["poll_hour"]["value"]["value"] = "14"
    register_command_handlers(app, session_factory, engine=MagicMock(), job_scheduler=MagicMock())

    app.views["settings_submit"](ack, {}, client, view)

    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        saved = json.loads(ch.schedule_json)
        assert saved["hour"] == 14
        assert saved["month_anchor_year"] == 2026
        assert saved["month_anchor_month"] == 5


def test_settings_submit_saves_automatic_execution_off(session_factory):
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
    view = _settings_view()
    view["state"]["values"]["automatic_execution"]["value"]["selected_option"]["value"] = "off"

    app.views["settings_submit"](ack, {}, client, view)

    ack.assert_called_once()
    scheduler.schedule_channel.assert_called_once_with(CHANNEL)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        assert ch.enabled is True
        assert ch.automatic_execution_enabled is False


def test_settings_submit_auto_off_without_schedule_fields_preserves_existing_schedule(session_factory):
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
    view = _settings_view()
    view["state"]["values"]["automatic_execution"]["value"]["selected_option"]["value"] = "off"
    for block_id in ("schedule_type", "day_of_month", "month_interval", "poll_hour"):
        view["state"]["values"].pop(block_id)

    app.views["settings_submit"](ack, {}, client, view)

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once()
    scheduler.schedule_channel.assert_called_once_with(CHANNEL)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        saved = json.loads(ch.schedule_json)
        assert saved["type"] == "WEEKLY_WEEKDAY"
        assert ch.automatic_execution_enabled is False


def test_settings_submit_auto_off_without_schedule_fields_uses_metadata_draft(session_factory):
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
    view = _settings_view()
    view["private_metadata"] = encode_settings_metadata(
        CHANNEL,
        {
            "type": "MONTHLY_NTH_WEEKDAY",
            "weekday": "4",
            "nth": "-1",
            "month_interval": "3",
            "hour": "16",
        },
    )
    view["state"]["values"]["automatic_execution"]["value"]["selected_option"]["value"] = "off"
    for block_id in ("schedule_type", "day_of_month", "month_interval", "poll_hour"):
        view["state"]["values"].pop(block_id)

    app.views["settings_submit"](ack, {}, client, view)

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once()
    scheduler.schedule_channel.assert_called_once_with(CHANNEL)
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        saved = json.loads(ch.schedule_json)
        assert saved["type"] == "MONTHLY_NTH_WEEKDAY"
        assert saved["weekday"] == 4
        assert saved["nth"] == -1
        assert saved["month_interval"] == 3
        assert saved["hour"] == 16
        assert ch.automatic_execution_enabled is False


def test_automatic_execution_change_hides_schedule_controls_immediately(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["value"](
        ack,
        {
            "view": {
                "id": "V_SETTINGS",
                "hash": "h_auto",
                "private_metadata": CHANNEL,
                "state": {
                    "values": {
                        "automatic_execution": {
                            "value": {"selected_option": {"value": "off"}}
                        },
                        "schedule_type": {
                            "value": {
                                "selected_option": {"value": "MONTHLY_DAY_OF_MONTH"}
                            }
                        },
                        "day_of_month": {"value": {"value": "20"}},
                        "month_interval": {"value": {"value": "3"}},
                        "poll_hour": {"value": {"value": "14"}},
                        "poll_duration": {"value": {"value": "48"}},
                    }
                },
            },
            "actions": [
                {
                    "block_id": "automatic_execution",
                    "action_id": "value",
                    "selected_option": {"value": "off"},
                }
            ],
        },
        client,
    )

    ack.assert_called_once()
    view = client.views_update.call_args.kwargs["view"]
    assert _block(view, "automatic_execution")["element"]["initial_option"]["value"] == "off"
    assert not any(block.get("block_id") == "schedule_type" for block in view["blocks"])
    assert not any(block.get("block_id") == "day_of_month" for block in view["blocks"])
    assert not any(block.get("block_id") == "month_interval" for block in view["blocks"])
    assert not any(block.get("block_id") == "poll_hour" for block in view["blocks"])
    assert _block(view, "poll_duration")["element"]["initial_value"] == "48"


def test_automatic_execution_change_from_off_preserves_non_schedule_inputs(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["value"](
        ack,
        {
            "view": {
                "id": "V_SETTINGS",
                "hash": "h_auto",
                "private_metadata": CHANNEL,
                "state": {
                    "values": {
                        "automatic_execution": {
                            "value": {"selected_option": {"value": "on"}}
                        },
                        "poll_duration": {"value": {"value": "72"}},
                        "booking_url": {"value": {"value": "https://example.com/book"}},
                    }
                },
            },
            "actions": [
                {
                    "block_id": "automatic_execution",
                    "action_id": "value",
                    "selected_option": {"value": "on"},
                }
            ],
        },
        client,
    )

    ack.assert_called_once()
    view = client.views_update.call_args.kwargs["view"]
    assert _block(view, "automatic_execution")["element"]["initial_option"]["value"] == "on"
    assert any(block.get("block_id") == "schedule_type" for block in view["blocks"])
    assert _block(view, "poll_duration")["element"]["initial_value"] == "72"
    assert _block(view, "booking_url")["element"]["initial_value"] == "https://example.com/book"


def test_automatic_execution_change_from_off_restores_persisted_schedule(session_factory):
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(CHANNEL)
        ch.automatic_execution_enabled = False
        ch.schedule_json = ScheduleSpec(
            type=ScheduleType.MONTHLY_NTH_WEEKDAY,
            weekday=4,
            nth=-1,
            month_interval=3,
            hour=16,
            minute=0,
        ).model_dump_json()
        session.commit()
    app = FakeApp()
    client = MagicMock()
    client.views_open.return_value = {"view": {"id": "V_SETTINGS"}}
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["open_settings"](
        MagicMock(),
        {"channel": {"id": CHANNEL}, "trigger_id": "TRIGGER1"},
        client,
    )
    opened_view = client.views_update.call_args.kwargs["view"]
    assert not any(block.get("block_id") == "schedule_type" for block in opened_view["blocks"])

    app.actions["value"](
        ack,
        {
            "view": {
                "id": "V_SETTINGS",
                "hash": "h_auto",
                "private_metadata": opened_view["private_metadata"],
                "state": {
                    "values": {
                        "automatic_execution": {
                            "value": {"selected_option": {"value": "on"}}
                        },
                        "poll_duration": {"value": {"value": "48"}},
                        "booking_url": {"value": {"value": "https://example.com/book"}},
                    }
                },
            },
            "actions": [
                {
                    "block_id": "automatic_execution",
                    "action_id": "value",
                    "selected_option": {"value": "on"},
                }
            ],
        },
        client,
    )

    ack.assert_called_once()
    view = client.views_update.call_args.kwargs["view"]
    assert _block(view, "schedule_type")["element"]["initial_option"]["value"] == "MONTHLY_NTH_WEEKDAY"
    assert _block(view, "month_interval")["element"]["initial_value"] == "3"
    assert _block(view, "nth")["element"]["initial_value"] == "-1"
    assert _block(view, "weekday")["element"]["initial_option"]["value"] == "4"
    assert _block(view, "poll_hour")["element"]["initial_value"] == "16"


def test_schedule_type_change_updates_settings_modal_fields(session_factory):
    app = FakeApp()
    client = MagicMock()
    ack = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["value"](
        ack,
        {
            "view": {
                "id": "V_SETTINGS",
                "hash": "h1",
                "private_metadata": CHANNEL,
                "state": {
                    "values": {
                        "automatic_execution": {
                            "value": {"selected_option": {"value": "on"}}
                        },
                        "schedule_type": {
                            "value": {
                                "selected_option": {"value": "MONTHLY_NTH_WEEKDAY"}
                            }
                        },
                        "weekday": {"value": {"selected_option": {"value": "1"}}},
                        "poll_hour": {"value": {"value": "14"}},
                        "poll_duration": {"value": {"value": "48"}},
                    }
                },
            },
            "actions": [
                {
                    "block_id": "schedule_type",
                    "action_id": "value",
                    "selected_option": {"value": "MONTHLY_NTH_WEEKDAY"},
                }
            ],
        },
        client,
    )

    ack.assert_called_once()
    updated = client.views_update.call_args.kwargs
    assert updated["view_id"] == "V_SETTINGS"
    assert updated["hash"] == "h1"
    view = updated["view"]
    assert _block(view, "schedule_type")["element"]["initial_option"]["value"] == "MONTHLY_NTH_WEEKDAY"
    assert _block(view, "weekday")["element"]["initial_option"]["value"] == "1"
    assert _block(view, "nth")["element"]["initial_value"] == "2"
    assert _block(view, "month_interval")["element"]["initial_value"] == "1"
    assert not any(block.get("block_id") == "day_of_month" for block in view["blocks"])


def test_schedule_type_change_preserves_hidden_monthly_day_draft(session_factory):
    app = FakeApp()
    client = MagicMock()
    register_event_handlers(app, session_factory, engine=MagicMock())

    app.actions["value"](
        MagicMock(),
        {
            "view": {
                "id": "V_SETTINGS",
                "hash": "h1",
                "private_metadata": CHANNEL,
                "state": {
                    "values": {
                        "automatic_execution": {
                            "value": {"selected_option": {"value": "on"}}
                        },
                        "schedule_type": {
                            "value": {
                                "selected_option": {"value": "MONTHLY_NTH_WEEKDAY"}
                            }
                        },
                        "day_of_month": {"value": {"value": "20"}},
                        "month_interval": {"value": {"value": "3"}},
                        "poll_hour": {"value": {"value": "14"}},
                        "poll_duration": {"value": {"value": "48"}},
                    }
                },
            },
            "actions": [
                {
                    "block_id": "schedule_type",
                    "action_id": "value",
                    "selected_option": {"value": "MONTHLY_NTH_WEEKDAY"},
                }
            ],
        },
        client,
    )
    first_view = client.views_update.call_args.kwargs["view"]
    assert not any(block.get("block_id") == "day_of_month" for block in first_view["blocks"])
    assert _block(first_view, "poll_hour")["element"]["initial_value"] == "14"
    assert first_view["private_metadata"] != CHANNEL

    client.reset_mock()
    app.actions["value"](
        MagicMock(),
        {
            "view": {
                "id": "V_SETTINGS",
                "hash": "h2",
                "private_metadata": first_view["private_metadata"],
                "state": {
                    "values": {
                        "automatic_execution": {
                            "value": {"selected_option": {"value": "on"}}
                        },
                        "schedule_type": {
                            "value": {
                                "selected_option": {"value": "MONTHLY_DAY_OF_MONTH"}
                            }
                        },
                        "weekday": {"value": {"selected_option": {"value": "1"}}},
                        "nth": {"value": {"value": "2"}},
                        "month_interval": {"value": {"value": "3"}},
                        "poll_hour": {"value": {"value": "14"}},
                        "poll_duration": {"value": {"value": "48"}},
                    }
                },
            },
            "actions": [
                {
                    "block_id": "schedule_type",
                    "action_id": "value",
                    "selected_option": {"value": "MONTHLY_DAY_OF_MONTH"},
                }
            ],
        },
        client,
    )

    second_view = client.views_update.call_args.kwargs["view"]
    assert _block(second_view, "day_of_month")["element"]["initial_value"] == "20"
    assert _block(second_view, "month_interval")["element"]["initial_value"] == "3"
    assert _block(second_view, "poll_hour")["element"]["initial_value"] == "14"
    assert not any(block.get("block_id") == "nth" for block in second_view["blocks"])


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


def test_poll_vote_action_success_does_not_post_ephemeral():
    app = FakeApp()
    engine = MagicMock()
    engine.on_poll_vote.return_value = PollVoteResult.success(added=True)
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
    client.chat_postEphemeral.assert_not_called()


def test_poll_vote_action_closed_posts_ephemeral_feedback():
    app = FakeApp()
    engine = MagicMock()
    engine.on_poll_vote.return_value = PollVoteResult.feedback(m.MSG_POLL_CLOSED)
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
    client.chat_postEphemeral.assert_called_once_with(
        channel=CHANNEL,
        user="U1",
        text=m.MSG_POLL_CLOSED,
    )


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
