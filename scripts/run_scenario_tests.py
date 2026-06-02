# -*- coding: utf-8 -*-
"""Run L2 API + engine scenario checks against a private Slack test channel."""
# ruff: noqa: E402
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slack_bolt import App

from app import messages as m
from app.config import settings
from app.db.models import init_db
from app.db.repository import ChannelRepository, WorkflowRepository
from app.handlers.intent import normalize_invocation_text
from app.slack_invocation import USER_CMD
from app.workflow.engine import WorkflowEngine
from app.workflow.states import WorkflowState

TEST_CHANNEL = os.getenv("TEAM_DINNER_BOT_TEST_CHANNEL_ID", "").strip()
FAILURES: list[str] = []
PASSED: list[str] = []


def ok(name: str) -> None:
    PASSED.append(name)
    print(f"  PASS  {name}")


def fail(name: str, detail: str) -> None:
    FAILURES.append(f"{name}: {detail}")
    print(f"  FAIL  {name} - {detail}")


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    include_public_close = _include_public_close(argv)
    print("=== L2 API scenario tests ===\n")
    if not TEST_CHANNEL:
        fail(
            "G1 test channel configuration",
            "set TEAM_DINNER_BOT_TEST_CHANNEL_ID to run live L2 Slack checks",
        )
        _summary()
        return 1
    if include_public_close:
        print("Mode: public close flow enabled (--include-public-close)\n")
    else:
        print("Mode: public-safe; no synthetic valid vote or close will be posted\n")

    for attr in (
        "MSG_POLL_STARTED",
        "MSG_CHANNEL_DISABLED",
        "MSG_POLL_ALREADY_OPEN",
        "MSG_POLL_CLOSED",
    ):
        if hasattr(m, attr):
            ok(f"G4 messages.{attr}")
        else:
            fail(f"G4 messages.{attr}", "missing")

    cases = [
        (USER_CMD, ""),
        (f"{USER_CMD} status", "status"),
        (f"/{USER_CMD} help", "help"),
        (f" /{USER_CMD}", ""),
        (f"<@BOT> {USER_CMD} \uc0c1\ud0dc", "\uc0c1\ud0dc"),
        ("noise", None),
    ]
    for raw, exp in cases:
        normalized_raw = raw
        got = normalize_invocation_text(normalized_raw)
        if got == exp:
            ok(f"A normalize {normalized_raw!r}")
        else:
            fail(f"A normalize {normalized_raw!r}", f"expected {exp!r}, got {got!r}")

    app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret)
    posted_messages: list[dict[str, Any]] = []
    original_post_message = app.client.chat_postMessage

    def recording_post_message(**kwargs):
        posted_messages.append(dict(kwargs))
        return original_post_message(**kwargs)

    app.client.chat_postMessage = recording_post_message
    try:
        auth = app.client.auth_test()
        ok(f"G2 auth_test bot={auth['user_id']}")
    except Exception as exc:
        fail("G2 auth_test", str(exc))
        _summary()
        return 1

    info = app.client.conversations_info(channel=TEST_CHANNEL)
    if info.get("channel", {}).get("is_member"):
        ok("G2 bot is channel member")
    else:
        fail("G2 channel membership", "bot not in channel")

    session_factory = init_db()
    engine = WorkflowEngine(session_factory, app.client)

    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(TEST_CHANNEL)
        if ch and ch.schedule_json:
            ok("C channel has schedule in DB")
        else:
            fail("C channel schedule", "no schedule")
        if ch and ch.poll_duration_hours == 24:
            ok("C default poll duration is 24 hours")
        elif ch:
            fail("C default poll duration", f"expected 24, got {ch.poll_duration_hours}")

    err = engine.start_channel_run(TEST_CHANNEL, replace=True)
    if err is None:
        ok("D4 start_channel_run (force)")
    elif err == m.MSG_POLL_ALREADY_OPEN:
        ok("D4 start_channel_run (poll already open)")
    else:
        fail("D4 start_channel_run", err or "unknown")
        _summary()
        return 1

    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(TEST_CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id) if ch else None
        if run:
            ok(f"D4 open run_id={run.id}")
            run_id = run.id
        else:
            fail("D4 open run", "no run row")
            _summary()
            return 1

    err3 = engine.start_channel_run(TEST_CHANNEL, replace=False)
    if err3 == m.MSG_POLL_ALREADY_OPEN:
        ok("D3 start_channel_run (duplicate blocked)")
    else:
        fail("D3 duplicate poll", repr(err3))

    poll_dates = _poll_dates_from_payloads(posted_messages)
    if not poll_dates:
        fail("D4 poll date buttons", "no poll dates found in Slack message payload")
        _summary()
        return 1
    poll_text = "\n".join(_payload_text(p) for p in posted_messages)
    if "투표 대상:" in poll_text:
        ok("D4 poll message shows vote targets")
    else:
        fail("D4 vote target list", "missing from Slack poll payload")
    with session_factory() as session:
        votes = WorkflowRepository(session).votes_by_user(run_id)
        if votes == {}:
            ok("D5 poll starts with zero votes")
        else:
            fail("D5 initial vote count", repr(votes))

    invalid_vote = engine.on_poll_vote(run_id, "U_TEST_VOTER", "1900-01-01", TEST_CHANNEL)
    if invalid_vote == m.MSG_INVALID_POLL_OPTION:
        ok("D5 invalid poll option rejected")
    else:
        fail("D5 invalid poll option", invalid_vote)
    with session_factory() as session:
        votes = WorkflowRepository(session).votes_by_user(run_id)
        if votes == {}:
            ok("D5 invalid poll option does not create a vote")
        else:
            fail("D5 invalid vote count", repr(votes))

    members_resp = app.client.conversations_members(channel=TEST_CHANNEL)
    human_ids = [
        m
        for m in members_resp.get("members", [])
        if m != auth["user_id"] and not m.startswith("B")
    ]
    voter = human_ids[0] if human_ids else "U_TEST_VOTER"

    if include_public_close:
        before_close_count = len(posted_messages)
        with patch(
            "app.workflow.engine.list_human_member_ids",
            lambda _c, _ch: human_ids or [voter],
        ), patch(
            "app.workflow.engine.collect_attendee_emails",
            lambda *_a: (["scenario@example.com"], []),
        ):
            engine.on_poll_vote(run_id, voter, poll_dates[-1], TEST_CHANNEL)
            engine.close_poll(run_id)

        with session_factory() as session:
            run = WorkflowRepository(session).get_run(run_id)
            if run.state == WorkflowState.BOOKING_ASSIGNED:
                ok("D7 close_poll with votes -> booking")
            else:
                fail("D7 close_poll", f"state={run.state}")

            close_payloads = posted_messages[before_close_count:]
            assignee = run.assignee_user_id or voter
            dm_payloads = [
                p
                for p in close_payloads
                if p.get("channel") == assignee
            ]
            public_payloads = [
                p
                for p in close_payloads
                if p.get("channel") == TEST_CHANNEL
            ]
            dm_text = "\n".join(_payload_text(p) for p in dm_payloads)
            public_text = "\n".join(_payload_text(p) for p in public_payloads)
            if "calendar.google.com/calendar/r/eventedit" in dm_text:
                ok("D7 booking DM contains Google Calendar link")
            else:
                fail("D7 booking DM calendar link", "missing from DM payload")
            if "calendar.google.com/calendar/r/eventedit" not in public_text:
                ok("D7 public channel has no calendar link")
            else:
                fail("D7 public calendar link", "calendar link leaked to public channel")

            done_msg = engine.on_booking_done(
                run_id,
                assignee,
                announce_channel=False,
            )
            if done_msg == m.MSG_BOOKING_DONE_OK:
                ok("D8 booking_done (no channel announce)")
            else:
                fail("D8 booking_done", done_msg)
    else:
        with session_factory() as session:
            run = WorkflowRepository(session).get_run(run_id)
            votes = WorkflowRepository(session).votes_by_user(run_id)
            if run.state == WorkflowState.POLL_OPEN and votes == {}:
                ok("D7 public close flow skipped; poll remains open with zero votes")
            else:
                fail("D7 public-safe state", f"state={run.state}, votes={votes!r}")

    _summary()
    return 1 if FAILURES else 0


def _summary() -> None:
    print(f"\n=== Summary: {len(PASSED)} passed, {len(FAILURES)} failed ===")
    for f in FAILURES:
        print(f"  - {f}")


def _payload_text(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("text") or "")]
    blocks = payload.get("blocks")
    if blocks:
        parts.append(repr(blocks))
    return "\n".join(parts)


def _poll_dates_from_payloads(payloads: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    for payload in payloads:
        for block in payload.get("blocks") or []:
            for element in block.get("elements", []):
                if str(element.get("action_id", "")).startswith("poll_vote_"):
                    dates.append(str(element["value"]).split(":", 1)[1])
    return dates


def _include_public_close(argv: list[str]) -> bool:
    enabled_values = {"1", "true", "yes", "on"}
    env_enabled = os.getenv("TEAM_DINNER_BOT_L2_INCLUDE_PUBLIC_CLOSE", "").strip().lower()
    return "--include-public-close" in argv or env_enabled in enabled_values


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
