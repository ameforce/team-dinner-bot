# -*- coding: utf-8 -*-
from __future__ import annotations

from app import messages as m
from app.config import settings
from app.handlers.intent import help_text


def test_public_runtime_defaults_are_neutral():
    assert settings.app_name == "Team Dinner Bot"
    assert settings.app_slug == "team-dinner-bot"
    assert settings.bot_display_name == "Team Dinner Bot"
    assert settings.database_url == "sqlite:///./data/team-dinner-bot.db"
    assert settings.lock_file_path == "./data/team-dinner-bot.lock"


def test_bot_name_comes_from_settings_default():
    assert m.BOT_NAME == settings.bot_display_name


def test_help_text_uses_slash_command_instruction():
    text = help_text()

    assert ("@e" + "Yami") not in text
    assert "/회식" in text
    assert "@봇이름" not in text
