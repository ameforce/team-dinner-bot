# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime

from app.workflow.dates import format_date_ko


def poll_blocks(
    run_id: int,
    candidate_dates: list[date],
    deadline: datetime,
    *,
    target_labels: list[str] | None = None,
    target_user_ids: list[str] | None = None,
    unavailable_by_user: dict[str, set[str]] | None = None,
) -> list[dict]:
    elements = []
    for d in candidate_dates:
        iso = d.isoformat()
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": format_date_ko(d)},
                "action_id": f"poll_vote_{iso.replace('-', '_')}",
                "value": f"{run_id}:{iso}",
            }
        )
    n = len(candidate_dates)
    intro = (
        "*\uc774\ubc88 \ub2ec \ud68c\uc2dd \ub0a0\uc9dc \ud22c\ud45c*\n"
        f"\uc624\ub298\ubd80\ud130 \uc774\ubc88 \ub2ec \ub9d0\uc77c\uae4c\uc9c0 *\uc601\uc5c5\uc77c(\uc6d4~\uae08) {n}\uc77c*\uc744 \ubc84\ud2bc\uc73c\ub85c \uc81c\uc2dc\ud588\uc2b5\ub2c8\ub2e4.\n"
        "\ubd88\uac00\ub2a5\ud55c \ub0a0\uc9dc\ub97c \uc120\ud0dd\ud574 \uc8fc\uc138\uc694. \uc5ec\ub7ec \uac1c \uc120\ud0dd\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.\n"
        f"\ub9c8\uac10: {deadline.strftime('%Y-%m-%d %H:%M')}"
    )
    if target_user_ids is not None:
        intro += f"\n{_format_target_line([_mention(uid) for uid in target_user_ids])}"
    elif target_labels is not None:
        intro += f"\n{_format_target_line(target_labels)}"
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": intro}},
    ]
    if unavailable_by_user is not None:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _format_unavailable_lines(candidate_dates, unavailable_by_user),
                },
            }
        )
    for i in range(0, len(elements), 5):
        blocks.append(
            {
                "type": "actions",
                "block_id": f"poll_{run_id}_{i // 5}",
                "elements": elements[i : i + 5],
            }
        )
    return blocks


def _mention(user_id: str) -> str:
    return f"<@{user_id}>"


def _format_target_line(target_labels: list[str], limit: int = 20) -> str:
    if not target_labels:
        return "투표 대상: 0명"
    visible = target_labels[:limit]
    extra = len(target_labels) - len(visible)
    suffix = f" 외 {extra}명" if extra > 0 else ""
    return f"투표 대상: {len(target_labels)}명 - {', '.join(visible)}{suffix}"


def _format_unavailable_lines(
    candidate_dates: list[date], unavailable_by_user: dict[str, set[str]]
) -> str:
    lines = ["*날짜별 불가능자*"]
    for d in candidate_dates:
        iso = d.isoformat()
        users = sorted(
            user_id for user_id, dates in unavailable_by_user.items() if iso in dates
        )
        names = ", ".join(_mention(user_id) for user_id in users) if users else "없음"
        lines.append(f"• {format_date_ko(d)} (`{iso}`): {names}")
    return "\n".join(lines)


def tally_votes(votes_by_user: dict[str, set[str]]) -> tuple[str | None, dict[str, int]]:
    winner, counts, _selection_pool = tally_votes_with_pool(votes_by_user)
    return winner, counts


def tally_votes_with_pool(
    votes_by_user: dict[str, set[str]]
) -> tuple[str | None, dict[str, int], list[str]]:
    counts: Counter[str] = Counter()
    for dates in votes_by_user.values():
        for d in dates:
            counts[d] += 1
    if not counts:
        return None, dict(counts), []
    max_votes = max(counts.values())
    tied = sorted(d for d, c in counts.items() if c == max_votes)
    return tied[0], dict(counts), tied


def choose_dinner_date(
    unavailable_by_user: dict[str, set[str]],
    candidate_date_isos: list[str],
    *,
    choose: Callable[[list[str]], str] = random.choice,
) -> tuple[str | None, dict[str, int]]:
    winner, counts, _selection_pool = choose_dinner_date_with_pool(
        unavailable_by_user,
        candidate_date_isos,
        choose=choose,
    )
    return winner, counts


def choose_dinner_date_with_pool(
    unavailable_by_user: dict[str, set[str]],
    candidate_date_isos: list[str],
    *,
    choose: Callable[[list[str]], str] = random.choice,
) -> tuple[str | None, dict[str, int], list[str]]:
    counts: Counter[str] = Counter({iso: 0 for iso in candidate_date_isos})
    valid = set(candidate_date_isos)
    for dates in unavailable_by_user.values():
        for date_iso in dates:
            if date_iso in valid:
                counts[date_iso] += 1
    if not candidate_date_isos:
        return None, dict(counts), []
    zero_unavailable = [iso for iso in candidate_date_isos if counts[iso] == 0]
    if zero_unavailable:
        return choose(zero_unavailable), dict(counts), zero_unavailable
    min_count = min(counts[iso] for iso in candidate_date_isos)
    best = [iso for iso in candidate_date_isos if counts[iso] == min_count]
    return choose(best), dict(counts), best


def winning_option_json(date_iso: str, counts: dict[str, int]) -> str:
    return json.dumps({"date": date_iso, "counts": counts}, ensure_ascii=False)


def format_tally_message(date_iso: str, counts: dict[str, int]) -> str:
    d = date.fromisoformat(date_iso)
    lines = [
        "*\ud22c\ud45c\uac00 \ub9c8\uac10\ub418\uc5c8\uc2b5\ub2c8\ub2e4.*\n"
        f"\ud655\uc815\uc77c: *{format_date_ko(d)}* (`{date_iso}`)"
    ]
    if counts:
        lines.append("\n*\ub0a0\uc9dc\ubcc4 \ubd88\uac00\ub2a5 \uc751\ub2f5*")
        for iso, n in sorted(counts.items(), key=lambda x: (x[1], x[0])):
            lines.append(f"\u2022 {format_date_ko(date.fromisoformat(iso))}: {n}\uba85")
    return "\n".join(lines)
