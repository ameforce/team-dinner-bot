# -*- coding: utf-8 -*-
"""Product-readiness scenarios without provisioning real Slack users."""
# ruff: noqa: E402
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.db.models import AssigneeHistory, Base, Channel
from app.db.repository import ChannelRepository, WorkflowRepository
from app.runtime_defaults import DEFAULT_TIMEZONE
from app.schedule.spec import ScheduleSpec, ScheduleType
from app.workflow.engine import WorkflowEngine
from app.workflow.states import WorkflowState

LOCAL_CHANNEL = "C_PRODUCT_READINESS"
FIXED_NOW = datetime(2026, 5, 21, 10, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
PUBLIC_PROBE_KEYS = (
    "can_create_test_users",
    "admin_users_invite_error",
    "scim_status_code",
    "reason",
)
PUBLIC_WORKSPACE_KEYS = (
    "team",
    "team_id",
    "bot_user_id",
    "bot_id",
)
HUMAN_USERS = [
    ("U_PR_1", "Product Tester 1"),
    ("U_PR_2", "Product Tester 2"),
    ("U_PR_3", "Product Tester 3"),
    ("U_PR_4", "Product Tester 4"),
]


def build_scenario_catalog() -> list[dict[str, str]]:
    return [
        {
            "id": "slack-provisioning-permission",
            "scenario": "실제 Slack 사용자/봇 4명 생성 가능 여부를 무해한 권한 probe로 확인",
        },
        {
            "id": "multi-user-vote-tally",
            "scenario": "4명의 독립 사용자 ID가 같은 날짜를 불가능으로 표시하고 정확히 4명으로 집계",
        },
        {
            "id": "vote-toggle",
            "scenario": "한 사용자가 같은 날짜를 다시 누르면 자신의 불가능 표시만 제거",
        },
        {
            "id": "duplicate-run",
            "scenario": "열린 투표가 있을 때 중복 투표 시작이 차단",
        },
        {
            "id": "cancel-active-run",
            "scenario": "진행 중 투표를 취소하면 활성 회차가 DONE으로 종료",
        },
        {
            "id": "no-vote-close",
            "scenario": "불가능 표시가 없는 투표 마감은 모두 가능한 날짜 중 하나를 선택",
        },
        {
            "id": "invalid-stale-vote",
            "scenario": "렌더링되지 않은 오래된 날짜 액션은 득표를 만들지 않음",
        },
        {
            "id": "random-booking-assignee",
            "scenario": "투표 마감 후 예약 담당자는 투표 대상 후보 중에서 랜덤 지정되고 직전 담당자는 제외",
        },
        {
            "id": "browser-session-preserved",
            "scenario": "Codex 인앱 브라우저 Slack 로그인 세션을 닫지 않고 실제 UI 확인",
        },
    ]


def classify_provisioning_probe(
    *,
    admin_invite_error: str | None,
    scim_status_code: int | None,
    scim_body: str,
) -> dict[str, Any]:
    admin_blocked = admin_invite_error in {
        "not_allowed_token_type",
        "missing_scope",
        "not_an_admin",
        "failed_to_validate_caller",
    }
    scim_blocked = scim_status_code in {401, 403} or "invalid_authentication" in scim_body
    can_create = not admin_blocked and not scim_blocked
    reason = (
        "admin.users.invite and SCIM appear usable"
        if can_create
        else (
            "admin.users.invite is not available for the current token "
            f"({admin_invite_error or 'unknown'}), and SCIM is not authenticated "
            f"(status={scim_status_code or 'unknown'})."
        )
    )
    return {
        "can_create_test_users": can_create,
        "admin_users_invite_error": admin_invite_error,
        "scim_status_code": scim_status_code,
        "reason": reason,
    }


def probe_slack_user_provisioning() -> dict[str, Any]:
    from app.config import settings

    client = WebClient(token=settings.slack_bot_token)
    auth = client.auth_test()
    try:
        resp = client.api_call("admin.users.invite", http_verb="POST", params={})
        admin_error = resp.get("error")
    except SlackApiError as exc:
        admin_error = exc.response.get("error")

    scim_status, scim_body = _scim_read_probe(settings.slack_bot_token)
    classified = classify_provisioning_probe(
        admin_invite_error=admin_error,
        scim_status_code=scim_status,
        scim_body=scim_body,
    )
    classified["workspace"] = {
        "team": auth.get("team"),
        "team_id": auth.get("team_id"),
        "bot_user_id": auth.get("user_id"),
        "bot_id": auth.get("bot_id"),
    }
    return classified


def run_local_multi_user_scenarios(db_path: str | Path) -> dict[str, Any]:
    checks: dict[str, str] = {}
    session_factory = _create_session_factory(Path(db_path))
    client = FakeSlackClient()
    engine = WorkflowEngine(session_factory, client)

    with patch("app.workflow.engine.datetime") as engine_datetime:
        engine_datetime.now.return_value = FIXED_NOW
        err = engine.start_channel_run(LOCAL_CHANNEL, replace=True)
    _check(checks, "start_poll", err is None, str(err))

    run_id = _open_run_id(session_factory)
    target_snapshot = _target_snapshot(session_factory, run_id)
    poll_text = _latest_poll_text(client)
    _check(checks, "poll_targets_four_humans", "투표 대상: 4명" in poll_text, poll_text)
    _check(
        checks,
        "target_snapshot_four_humans",
        target_snapshot == _human_ids(),
        repr(target_snapshot),
    )
    _check(checks, "bot_excluded_from_targets", "Automation Bot" not in poll_text, poll_text)
    _check(checks, "deleted_user_excluded_from_targets", "Deleted Tester" not in poll_text, poll_text)

    dup = engine.start_channel_run(LOCAL_CHANNEL, replace=False)
    _check(checks, "duplicate_poll_guard", dup == m.MSG_POLL_ALREADY_OPEN, str(dup))

    invalid = engine.on_poll_vote(run_id, "U_PR_1", "2099-06-20", LOCAL_CHANNEL)
    _check(
        checks,
        "invalid_vote_rejected",
        invalid.needs_feedback and invalid.feedback_text == m.MSG_INVALID_POLL_OPTION,
        str(invalid),
    )
    _check(checks, "invalid_vote_does_not_count", _votes(session_factory, run_id) == {}, "vote leaked")

    poll_dates = _poll_dates(client)
    first_date = poll_dates[0]
    second_date = poll_dates[1]
    for user_id, _label in HUMAN_USERS:
        engine.on_poll_vote(run_id, user_id, first_date, LOCAL_CHANNEL)
    engine.on_poll_vote(run_id, "U_PR_1", second_date, LOCAL_CHANNEL)
    engine.on_poll_vote(run_id, "U_PR_1", second_date, LOCAL_CHANNEL)
    votes = _votes(session_factory, run_id)
    _check(checks, "four_users_vote_and_tally", len(votes) == 4, repr(votes))
    _check(checks, "toggle_removes_one_vote", votes["U_PR_1"] == {first_date}, repr(votes))

    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(LOCAL_CHANNEL)
        session.add(AssigneeHistory(channel_id=ch.id, user_id="U_PR_1"))
        session.commit()
    chosen_pools: list[list[str]] = []

    def choose(pool: list[str]) -> str:
        chosen_pools.append(list(pool))
        return pool[-1]

    with patch("app.workflow.engine.random.choice", choose):
        engine.close_poll(run_id)
    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assignee = run.assignee_user_id
    _check(checks, "booking_assignee_random_candidate", assignee in _human_ids(), str(assignee))
    _check(checks, "booking_avoids_previous_assignee", "U_PR_1" not in chosen_pools[-1], repr(chosen_pools))
    _check(checks, "booking_done", engine.on_booking_done(run_id, assignee, announce_channel=False) == m.MSG_BOOKING_DONE_OK, str(assignee))

    with patch("app.workflow.engine.datetime") as engine_datetime:
        engine_datetime.now.return_value = FIXED_NOW
        engine.start_channel_run(LOCAL_CHANNEL, replace=True)
    cancel_run_id = _open_run_id(session_factory)
    cancel_msg = engine.cancel_current_run(LOCAL_CHANNEL)
    _check(checks, "cancel_active_run", cancel_msg == m.MSG_RUN_CANCELLED, cancel_msg)
    _check(checks, "cancel_marks_done", _run_state(session_factory, cancel_run_id) == WorkflowState.DONE, _run_state(session_factory, cancel_run_id))

    with patch("app.workflow.engine.datetime") as engine_datetime:
        engine_datetime.now.return_value = FIXED_NOW
        engine.start_channel_run(LOCAL_CHANNEL, replace=True)
    no_vote_run_id = _open_run_id(session_factory)
    engine.close_poll(no_vote_run_id)
    _check(
        checks,
        "no_vote_close",
        _run_state(session_factory, no_vote_run_id) == WorkflowState.BOOKING_ASSIGNED,
        _run_state(session_factory, no_vote_run_id),
    )

    return {
        "ok": all(value == "passed" for value in checks.values()),
        "voter_count": len(HUMAN_USERS),
        "checks": checks,
        "posted_messages": len(client.posts),
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    live_probe = "--live-probe" in argv
    include_workspace_identifiers = "--include-workspace-identifiers" in argv
    report: dict[str, Any] = {
        "scenario_catalog": build_scenario_catalog(),
        "local_multi_user": run_local_multi_user_scenarios(
            ROOT / ".omx" / "ultragoal" / "product-readiness-local.db"
        ),
    }
    if live_probe:
        report["slack_provisioning_probe"] = _public_probe_report(
            probe_slack_user_provisioning(),
            include_workspace_identifiers=include_workspace_identifiers,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["local_multi_user"]["ok"] else 1


def _public_probe_report(
    probe: dict[str, Any],
    *,
    include_workspace_identifiers: bool = False,
) -> dict[str, Any]:
    report = {key: probe[key] for key in PUBLIC_PROBE_KEYS if key in probe}
    if include_workspace_identifiers and isinstance(probe.get("workspace"), dict):
        workspace = probe["workspace"]
        report["workspace"] = {
            key: workspace[key] for key in PUBLIC_WORKSPACE_KEYS if key in workspace
        }
    return report


class FakeSlackClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def conversations_members(self, channel: str) -> dict[str, Any]:
        return {"members": _human_ids() + ["U_PR_BOT", "U_PR_DELETED", "USLACKBOT"]}

    def users_info(self, user: str) -> dict[str, Any]:
        labels = dict(HUMAN_USERS)
        if user == "U_PR_BOT":
            return _user(user, "Automation Bot", is_bot=True)
        if user == "U_PR_DELETED":
            return _user(user, "Deleted Tester", deleted=True)
        if user == "USLACKBOT":
            return _user(user, "Slackbot")
        return _user(user, labels.get(user, user), email=f"{user.lower()}@example.com")

    def chat_postMessage(self, **kwargs) -> dict[str, Any]:
        self.posts.append(dict(kwargs))
        return {"ok": True, "ts": f"1779330000.{len(self.posts):06d}"}


def _create_session_factory(db_path: Path):
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add(
            Channel(
                team_id="T_PRODUCT",
                channel_id=LOCAL_CHANNEL,
                enabled=True,
                schedule_json=ScheduleSpec(
                    type=ScheduleType.WEEKLY_WEEKDAY,
                    weekday=1,
                    hour=10,
                    minute=0,
                ).model_dump_json(),
                poll_duration_hours=24,
                tz=DEFAULT_TIMEZONE,
            )
        )
        session.commit()
    return factory


def _check(checks: dict[str, str], name: str, condition: bool, detail: str) -> None:
    checks[name] = "passed" if condition else f"failed: {detail}"


def _human_ids() -> list[str]:
    return [user_id for user_id, _label in HUMAN_USERS]


def _user(
    user_id: str,
    display_name: str,
    *,
    is_bot: bool = False,
    deleted: bool = False,
    email: str | None = None,
) -> dict[str, Any]:
    return {
        "user": {
            "id": user_id,
            "is_bot": is_bot,
            "deleted": deleted,
            "profile": {
                "display_name": display_name,
                "email": email,
            },
            "real_name": display_name,
            "name": display_name.lower().replace(" ", "."),
        }
    }


def _open_run_id(session_factory) -> int:
    with session_factory() as session:
        ch = ChannelRepository(session).get_by_channel_id(LOCAL_CHANNEL)
        run = WorkflowRepository(session).get_open_run(ch.id)
        if not run:
            raise AssertionError("open run not found")
        return run.id


def _run_state(session_factory, run_id: int) -> str:
    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        return run.state


def _votes(session_factory, run_id: int) -> dict[str, set[str]]:
    with session_factory() as session:
        return WorkflowRepository(session).votes_by_user(run_id)


def _target_snapshot(session_factory, run_id: int) -> list[str]:
    with session_factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        return json.loads(run.target_member_ids_json or "[]")


def _latest_poll_text(client: FakeSlackClient) -> str:
    return "\n".join(_payload_text(post) for post in client.posts)


def _payload_text(payload: dict[str, Any]) -> str:
    parts = [str(payload.get("text") or "")]
    for block in payload.get("blocks") or []:
        text = block.get("text", {})
        if isinstance(text, dict):
            parts.append(str(text.get("text") or ""))
    return "\n".join(parts)


def _poll_dates(client: FakeSlackClient) -> list[str]:
    dates: list[str] = []
    for payload in client.posts:
        for block in payload.get("blocks") or []:
            for element in block.get("elements", []):
                if str(element.get("action_id", "")).startswith("poll_vote_"):
                    dates.append(str(element["value"]).split(":", 1)[1])
    if len(dates) < 2:
        raise AssertionError("expected at least two poll dates")
    return dates


def _scim_read_probe(token: str) -> tuple[int | None, str]:
    req = urllib.request.Request(
        "https://api.slack.com/scim/v1/Users?count=1&startIndex=1",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return None, str(exc)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
