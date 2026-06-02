# -*- coding: utf-8 -*-
from datetime import date

from datetime import datetime

from app.workflow.poll import choose_dinner_date, poll_blocks, tally_votes


def test_tally_plurality():
    votes = {
        "U1": {"2026-06-10", "2026-06-17"},
        "U2": {"2026-06-10"},
        "U3": {"2026-06-17"},
    }
    winner, counts = tally_votes(votes)
    assert winner == "2026-06-10"
    assert counts["2026-06-10"] == 2


def test_choose_dinner_date_prefers_zero_unavailable_dates_randomly():
    votes = {
        "U1": {"2026-06-10"},
        "U2": {"2026-06-10", "2026-06-17"},
    }
    choices: list[list[str]] = []

    def choose(options: list[str]) -> str:
        choices.append(options)
        return options[-1]

    winner, counts = choose_dinner_date(
        votes,
        ["2026-06-10", "2026-06-17", "2026-06-24", "2026-06-25"],
        choose=choose,
    )

    assert winner == "2026-06-25"
    assert choices == [["2026-06-24", "2026-06-25"]]
    assert counts == {
        "2026-06-10": 2,
        "2026-06-17": 1,
        "2026-06-24": 0,
        "2026-06-25": 0,
    }


def test_choose_dinner_date_falls_back_to_minimum_unavailable_randomly():
    votes = {
        "U1": {"2026-06-10", "2026-06-17"},
        "U2": {"2026-06-10", "2026-06-24"},
    }
    choices: list[list[str]] = []

    def choose(options: list[str]) -> str:
        choices.append(options)
        return options[0]

    winner, counts = choose_dinner_date(
        votes,
        ["2026-06-10", "2026-06-17", "2026-06-24"],
        choose=choose,
    )

    assert winner == "2026-06-17"
    assert choices == [["2026-06-17", "2026-06-24"]]
    assert counts["2026-06-10"] == 2
    assert counts["2026-06-17"] == 1
    assert counts["2026-06-24"] == 1


def test_tally_tie_earliest():
    votes = {"U1": {"2026-06-20"}, "U2": {"2026-06-15"}}
    winner, _ = tally_votes(votes)
    assert winner == "2026-06-15"


def test_poll_blocks_unique_action_ids():
    dates = [date(2026, 6, d) for d in range(10, 15)]
    blocks = poll_blocks(1, dates, datetime(2026, 6, 1, 12, 0))
    action_ids = []
    for block in blocks:
        if block.get("type") == "actions":
            for el in block.get("elements", []):
                action_ids.append(el["action_id"])
    assert len(action_ids) == len(set(action_ids))
    assert all(a.startswith("poll_vote_") for a in action_ids)


def test_poll_blocks_mention_vote_targets_and_show_unavailable_voters():
    dates = [date(2026, 6, d) for d in range(10, 12)]
    blocks = poll_blocks(
        1,
        dates,
        datetime(2026, 6, 1, 12, 0),
        target_user_ids=["U1", "U2"],
        unavailable_by_user={"U1": {"2026-06-10"}, "U2": {"2026-06-11"}},
    )

    intro = blocks[0]["text"]["text"]
    assert "투표 대상: 2명" in intro
    assert "<@U1>, <@U2>" in intro
    all_text = "\n".join(
        block.get("text", {}).get("text", "")
        for block in blocks
        if block.get("type") == "section"
    )
    assert "불가능한 날짜를 선택" in all_text
    assert "2026-06-10" in all_text
    assert "<@U1>" in all_text


def test_tally_empty():
    winner, counts = tally_votes({})
    assert winner is None
    assert counts == {}
