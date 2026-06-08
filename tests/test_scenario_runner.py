# -*- coding: utf-8 -*-
"""Regression tests for the live Slack scenario runner."""
from __future__ import annotations

from app import messages as m
from app.workflow.engine import PollVoteResult
from scripts import run_scenario_tests as runner


def test_l2_public_close_flow_is_opt_in_by_default(monkeypatch):
    monkeypatch.delenv("TEAM_DINNER_BOT_L2_INCLUDE_PUBLIC_CLOSE", raising=False)

    assert not runner._include_public_close([])


def test_l2_public_close_flow_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.delenv("TEAM_DINNER_BOT_L2_INCLUDE_PUBLIC_CLOSE", raising=False)

    assert runner._include_public_close(["--include-public-close"])


def test_l2_public_close_flow_supports_env_override(monkeypatch):
    monkeypatch.setenv("TEAM_DINNER_BOT_L2_INCLUDE_PUBLIC_CLOSE", "1")

    assert runner._include_public_close([])


def test_l2_runner_has_no_default_live_channel():
    assert runner.TEST_CHANNEL == ""


def test_l2_runner_matches_poll_vote_feedback_result():
    result = PollVoteResult.feedback(m.MSG_INVALID_POLL_OPTION)

    assert runner._poll_vote_feedback_matches(result, m.MSG_INVALID_POLL_OPTION)
