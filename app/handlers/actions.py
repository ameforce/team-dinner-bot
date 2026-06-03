# -*- coding: utf-8 -*-
from __future__ import annotations

import re

from slack_bolt import App
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.workflow.engine import WorkflowEngine


def register_action_handlers(app: App, session_factory: sessionmaker, engine: WorkflowEngine) -> None:
    @app.action(re.compile(r"^poll_vote_"))
    def on_poll_vote(ack, body, client):
        ack()
        user_id, channel_id = _action_context(body)
        if not user_id or not channel_id:
            _post_invalid_action(client, channel_id, user_id)
            return
        try:
            value = _first_action_value(body)
            run_id_s, date_iso = value.split(":", 1)
            if not date_iso:
                raise ValueError("date missing")
            run_id = int(run_id_s)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            _post_invalid_action(client, channel_id, user_id)
            return
        msg = engine.on_poll_vote(run_id, user_id, date_iso, channel_id)
        client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)

    @app.action("booking_done")
    def on_booking_done(ack, body, client):
        ack()
        user_id, channel_id = _action_context(body)
        if not user_id or not channel_id:
            _post_invalid_action(client, channel_id, user_id)
            return
        try:
            run_id = int(_first_action_value(body))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            _post_invalid_action(client, channel_id, user_id)
            return
        msg = engine.on_booking_done(run_id, user_id)
        if channel_id:
            client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)


def _first_action_value(body: dict) -> str:
    actions = body.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("action missing")
    action = actions[0]
    if not isinstance(action, dict):
        raise ValueError("action invalid")
    value = action.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("action value missing")
    return value


def _action_context(body: dict) -> tuple[str | None, str | None]:
    if not isinstance(body, dict):
        return None, None
    user = body.get("user") or {}
    channel = body.get("channel") or {}
    user_id = user.get("id") if isinstance(user, dict) else None
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    if isinstance(user_id, str):
        user_id = user_id.strip()
    if isinstance(channel_id, str):
        channel_id = channel_id.strip()
    if not isinstance(user_id, str) or not user_id:
        user_id = None
    if not isinstance(channel_id, str) or not channel_id:
        channel_id = None
    return user_id, channel_id


def _post_invalid_action(client, channel_id: str | None, user_id: str | None) -> None:
    if channel_id and user_id:
        client.chat_postEphemeral(channel=channel_id, user=user_id, text=m.MSG_ACTION_INVALID)
