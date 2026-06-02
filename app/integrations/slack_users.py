# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import UserEmailMap

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlackHumanMember:
    user_id: str
    display_name: str


def list_human_members(client, channel_id: str) -> list[SlackHumanMember]:
    """Return non-bot channel members with display labels."""
    resp = client.conversations_members(channel=channel_id)
    members = resp.get("members", [])
    humans: list[SlackHumanMember] = []
    for uid in members:
        try:
            info = client.users_info(user=uid)
            user = info.get("user", {})
            if user.get("is_bot") or user.get("deleted"):
                continue
            if user.get("id") == "USLACKBOT":
                continue
            profile = user.get("profile", {}) or {}
            label = (
                profile.get("display_name")
                or user.get("real_name")
                or user.get("name")
                or uid
            )
            humans.append(SlackHumanMember(user_id=uid, display_name=label))
        except Exception:
            logger.exception("users_info failed for %s", uid)
    return humans


def list_human_member_ids(client, channel_id: str) -> list[str]:
    """Return non-bot member user IDs for a channel."""
    return [member.user_id for member in list_human_members(client, channel_id)]


def get_user_email(client, user_id: str) -> str | None:
    try:
        info = client.users_info(user=user_id)
        profile = info.get("user", {}).get("profile", {})
        return profile.get("email")
    except Exception:
        logger.exception("email lookup failed for %s", user_id)
        return None


def upsert_email_map(session: Session, slack_user_id: str, email: str) -> None:
    row = session.get(UserEmailMap, slack_user_id)
    if row:
        row.email = email
    else:
        session.add(UserEmailMap(slack_user_id=slack_user_id, email=email, source="slack_profile"))
    session.commit()


def collect_attendee_emails(
    session: Session, client, member_ids: list[str]
) -> tuple[list[str], list[str]]:
    emails: list[str] = []
    missing: list[str] = []
    for uid in member_ids:
        row = session.get(UserEmailMap, uid)
        email = row.email if row else get_user_email(client, uid)
        if email:
            emails.append(email)
            if not row:
                upsert_email_map(session, uid, email)
        else:
            missing.append(uid)
    return emails, missing
