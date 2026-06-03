# -*- coding: utf-8 -*-
"""Seed weekly schedule for a private Slack test channel (L2/E2E prerequisite)."""
# ruff: noqa: E402
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.models import Channel, init_db
from app.db.repository import ChannelRepository
from app.config import settings
from app.schedule.spec import ScheduleSpec, ScheduleType

TEST_CHANNEL = os.getenv("TEAM_DINNER_BOT_TEST_CHANNEL_ID", "").strip()
TEAM_ID = os.getenv("TEAM_DINNER_BOT_TEST_TEAM_ID", "").strip()


def main() -> None:
    if not TEST_CHANNEL or not TEAM_ID:
        raise SystemExit(
            "Set TEAM_DINNER_BOT_TEST_CHANNEL_ID and TEAM_DINNER_BOT_TEST_TEAM_ID "
            "before seeding a live test channel."
        )
    spec = ScheduleSpec(type=ScheduleType.WEEKLY_WEEKDAY, weekday=1, hour=10, minute=0)
    schedule_json = json.dumps(spec.model_dump(mode="json"))
    factory = init_db()
    with factory() as session:
        repo = ChannelRepository(session)
        ch = repo.get_by_channel_id(TEST_CHANNEL)
        if not ch:
            ch = Channel(
                team_id=TEAM_ID,
                channel_id=TEST_CHANNEL,
                enabled=True,
                schedule_json=schedule_json,
                poll_duration_hours=24,
                tz=settings.default_timezone,
            )
            session.add(ch)
        else:
            ch.enabled = True
            ch.schedule_json = schedule_json
            ch.poll_duration_hours = 24
            ch.tz = settings.default_timezone
        session.commit()
    print(f"OK: schedule saved for {TEST_CHANNEL}: {spec.describe_ko()}")


if __name__ == "__main__":
    main()
