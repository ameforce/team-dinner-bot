# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CalendarInvitee:
    value: str
    role: str = "required"
    kind: str = "slack"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def resolve_poll_target_ids(
    *,
    configured_target_ids: list[str],
    known_member_ids: list[str],
    current_member_ids: list[str],
) -> list[str]:
    known = set(known_member_ids)
    current = set(current_member_ids)
    resolved: list[str] = []
    for user_id in configured_target_ids:
        if user_id in known and user_id not in current:
            continue
        resolved.append(user_id)
    for user_id in current_member_ids:
        if user_id not in known:
            resolved.append(user_id)
    return _unique(resolved)


def resolve_calendar_invitees(
    *,
    configured_invitees: list[CalendarInvitee],
    known_member_ids: list[str],
    current_member_ids: list[str],
) -> list[CalendarInvitee]:
    known = set(known_member_ids)
    current = set(current_member_ids)
    resolved: list[CalendarInvitee] = []
    configured_slack_ids: set[str] = set()
    for invitee in configured_invitees:
        if invitee.kind == "slack":
            configured_slack_ids.add(invitee.value)
            if invitee.value in known and invitee.value not in current:
                continue
        resolved.append(invitee)
    for user_id in current_member_ids:
        if user_id not in known and user_id not in configured_slack_ids:
            resolved.append(CalendarInvitee(value=user_id, role="required", kind="slack"))
    return resolved


def ids_to_json(user_ids: list[str]) -> str:
    return json.dumps(_unique(user_ids), ensure_ascii=False)


def ids_from_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if isinstance(item, str) and item]


def invitees_to_json(invitees: list[CalendarInvitee]) -> str:
    return json.dumps(
        [
            {"kind": invitee.kind, "role": invitee.role, "value": invitee.value}
            for invitee in invitees
        ],
        ensure_ascii=False,
    )


def invitees_from_json(raw: str | None) -> list[CalendarInvitee]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    invitees: list[CalendarInvitee] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if not isinstance(value, str) or not value:
            continue
        role = item.get("role") if isinstance(item.get("role"), str) else "required"
        kind = item.get("kind") if isinstance(item.get("kind"), str) else "slack"
        invitees.append(CalendarInvitee(value=value, role=role, kind=kind))
    return invitees
