# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import Base, Channel
from app.settings_defaults import DEFAULT_TIMEZONE


def test_runtime_default_timezone_comes_from_canonical_default():
    assert settings.default_timezone == DEFAULT_TIMEZONE


def test_channel_timezone_default_follows_runtime_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "default_timezone", "Etc/UTC")

    engine = create_engine(f"sqlite:///{tmp_path / 'tz.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        channel = Channel(
            team_id="T_TEST",
            channel_id="C_TEST",
            enabled=True,
            schedule_json=json.dumps({}),
            poll_duration_hours=24,
        )
        session.add(channel)
        session.commit()
        session.refresh(channel)

        assert channel.tz == "Etc/UTC"
