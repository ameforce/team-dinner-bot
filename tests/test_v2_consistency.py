# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import messages as m
from app.db.models import (
    Base,
    Channel,
    ChannelRunClaim,
    OutboundEffect,
    WorkflowRun,
    _migrate_existing_sqlite,
    init_db,
)
from app.db.repository import ChannelRepository, WorkflowRepository
from app.integrations.google_calendar import CalendarCreateResult
from app.rendering import (
    MessageAudience,
    RenderedSlackMessage,
    ResultCode,
    render_poll_result,
    render_settings_saved,
    render_status,
    render_welcome,
)
from app.schedule.spec import ScheduleSpec, ScheduleType
from app.scheduler.runner import JobScheduler
from app.workflow.engine import WorkflowEngine
from app.workflow.states import (
    CalendarOutcome,
    OutboundEffectStatus,
    OutboundEffectType,
    WorkflowState,
)


CHANNEL = "C_V2"


@pytest.fixture()
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'v2.db'}", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    with session_factory() as session:
        session.add(
            Channel(
                team_id="T1",
                channel_id=CHANNEL,
                enabled=True,
                schedule_json=ScheduleSpec(
                    type=ScheduleType.MONTHLY_DAY_OF_MONTH,
                    day=1,
                    hour=10,
                    minute=0,
                ).model_dump_json(),
                poll_duration_hours=24,
                tz="Asia/Seoul",
                poll_target_ids_json=json.dumps(["U1"]),
                channel_member_ids_json=json.dumps(["U1"]),
            )
        )
        session.commit()
    return session_factory


def _slack_client() -> MagicMock:
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "111.222"}
    client.conversations_members.return_value = {"members": ["U1"]}
    client.users_info.return_value = {
        "user": {
            "id": "U1",
            "is_bot": False,
            "deleted": False,
            "profile": {"display_name": "user-one", "email": "u1@example.com"},
            "real_name": "User One",
            "name": "u1",
        }
    }
    return client


def test_disabled_settings_save_is_exact_multiline_without_cadence_claim():
    rendered = render_settings_saved(
        automatic_enabled=False,
        schedule_description="매월 1일 10:00",
        poll_duration_hours=24,
        scheduler_applied=True,
        next_run=None,
    )

    assert rendered == RenderedSlackMessage(
        ResultCode.SETTINGS_SAVED,
        MessageAudience.PUBLIC,
        "설정을 저장했습니다.\n• 자동 실행: 사용 안 함\n• 투표 마감: 시작 후 24시간",
    )
    assert "실행 일정" not in rendered.text
    assert "다음 실행" not in rendered.text


def test_disabled_settings_warns_when_scheduler_stop_is_not_confirmed():
    rendered = render_settings_saved(
        automatic_enabled=False,
        schedule_description="매월 1일 10:00",
        poll_duration_hours=24,
        scheduler_applied=False,
        next_run=None,
    )

    assert rendered.result_code == ResultCode.SETTINGS_SAVED_SCHEDULER_PENDING
    assert "자동 실행: 사용 안 함" in rendered.text
    assert "자동 실행 중지 반영 지연" in rendered.text
    assert "실행 일정:" not in rendered.text


def test_disabled_status_warns_when_scheduler_stop_is_not_confirmed():
    rendered = render_status(
        schedule_description="매월 1일 10:00",
        automatic_enabled=False,
        poll_duration_hours=24,
        timezone_name="Asia/Seoul",
        scheduler_applied=False,
        next_run=None,
    )

    assert "자동 실행: 사용 안 함" in rendered.text
    assert "자동 실행 중지 반영 지연" in rendered.text
    assert "다음 실행:" not in rendered.text


def test_poll_result_calls_votes_unavailable_and_winner_confirmed_date():
    rendered = render_poll_result(
        "2026-08-14",
        {"2026-08-14": 0, "2026-08-15": 2},
    )

    assert "확정일:" in rendered.text
    assert "날짜별 불가능 응답" in rendered.text
    assert "득표" not in rendered.text
    assert "확정 후보" not in rendered.text


@pytest.mark.parametrize(
    ("automatic_enabled", "scheduler_applied"),
    [(False, True), (True, False)],
)
def test_welcome_does_not_claim_automatic_management_when_not_applied(
    automatic_enabled,
    scheduler_applied,
):
    rendered = render_welcome(
        automatic_enabled=automatic_enabled,
        scheduler_applied=scheduler_applied,
    )

    assert "회식 일정을 자동으로 관리합니다" not in rendered.text


def test_disabled_welcome_warns_when_scheduler_stop_is_not_confirmed():
    rendered = render_welcome(
        automatic_enabled=False,
        scheduler_applied=False,
    )

    assert rendered.result_code == ResultCode.WELCOME_SCHEDULER_PENDING
    assert "자동 실행: 사용 안 함" in rendered.text
    assert "자동 실행 중지 반영이 확인되지 않았습니다" in rendered.text


def test_claimed_run_allows_only_one_nonterminal_run_per_channel(factory):
    with factory() as session:
        channel_id = ChannelRepository(session).get_by_channel_id(CHANNEL).id

    def create_one():
        with factory() as session:
            return WorkflowRepository(session).create_claimed_run(
                channel_id,
                state=WorkflowState.POLL_STARTING,
                initial_effect_type=OutboundEffectType.POLL_OPEN_MESSAGE,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _unused: create_one(), range(2)))

    assert sum(result is not None for result in results) == 1
    with factory() as session:
        assert len(session.scalars(select(ChannelRunClaim)).all()) == 1
        assert (
            len(
                session.scalars(
                    select(OutboundEffect).where(
                        OutboundEffect.effect_type
                        == OutboundEffectType.POLL_OPEN_MESSAGE
                    )
                ).all()
            )
            == 1
        )


def test_scheduler_join_and_leave_are_symmetric_and_ledgered(factory):
    engine = WorkflowEngine(factory, _slack_client())
    scheduler = JobScheduler(factory, engine)

    added = scheduler.schedule_channel(CHANNEL)
    assert added.applied is True
    assert scheduler.scheduler.get_job(f"channel_run_{CHANNEL}") is not None

    with factory() as session:
        ChannelRepository(session).disable_channel(CHANNEL)
    removed = scheduler.schedule_channel(CHANNEL)
    assert removed.applied is True
    assert scheduler.scheduler.get_job(f"channel_run_{CHANNEL}") is None

    with factory() as session:
        effects = session.scalars(
            select(OutboundEffect).where(
                OutboundEffect.effect_type == OutboundEffectType.SCHEDULER_SYNC
            )
        ).all()
        assert [effect.status for effect in effects] == [
            OutboundEffectStatus.SENT,
            OutboundEffectStatus.SENT,
        ]


def test_sqlite_legacy_open_run_backfills_claim_and_unknown_poll_effect(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-v2.db'}", future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE channels (
                id INTEGER PRIMARY KEY,
                team_id VARCHAR(32),
                channel_id VARCHAR(32),
                enabled BOOLEAN,
                schedule_json TEXT,
                poll_duration_hours INTEGER,
                tz VARCHAR(64),
                booking_url_template TEXT,
                created_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE workflow_runs (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                state VARCHAR(32),
                thread_ts VARCHAR(32)
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO channels(id, team_id, channel_id, enabled) "
            "VALUES (1, 'T1', 'C1', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_runs(id, channel_id, state, thread_ts) "
            "VALUES (7, 1, 'POLL_OPEN', NULL)"
        )

    _migrate_existing_sqlite(engine)

    with engine.begin() as connection:
        claim = connection.exec_driver_sql(
            "SELECT channel_id, run_id FROM channel_run_claims"
        ).mappings().one()
        effect = connection.exec_driver_sql(
            "SELECT effect_type, status FROM outbound_effects"
        ).mappings().one()
        version = connection.exec_driver_sql(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).scalar_one()
        state = connection.exec_driver_sql(
            "SELECT state FROM workflow_runs WHERE id=7"
        ).scalar_one()
    assert dict(claim) == {"channel_id": 1, "run_id": 7}
    assert dict(effect) == {
        "effect_type": OutboundEffectType.POLL_OPEN_MESSAGE,
        "status": OutboundEffectStatus.UNKNOWN,
    }
    assert version == "2"
    assert state == WorkflowState.NEEDS_ATTENTION


def test_sqlite_migration_stops_on_duplicate_active_runs(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicate-v2.db'}", future=True)
    Base.metadata.create_all(engine)
    duplicate_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with duplicate_factory() as session:
        channel = Channel(
            team_id="T1",
            channel_id="C1",
            enabled=True,
            schedule_json=None,
        )
        session.add(channel)
        session.flush()
        session.add_all(
            [
                WorkflowRun(channel_id=channel.id, state=WorkflowState.POLL_OPEN),
                WorkflowRun(
                    channel_id=channel.id,
                    state=WorkflowState.CLOSE_COMPUTED,
                ),
            ]
        )
        session.commit()

    with pytest.raises(RuntimeError, match="manual reconciliation"):
        _migrate_existing_sqlite(engine)


def test_sqlite_duplicate_preflight_stops_before_additive_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicate-legacy.db'}", future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE workflow_runs (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                state VARCHAR(32)
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_runs(id, channel_id, state) VALUES "
            "(1, 7, 'POLL_OPEN'), (2, 7, 'BOOKING_ASSIGNED')"
        )

    with pytest.raises(RuntimeError, match="manual reconciliation"):
        _migrate_existing_sqlite(engine)

    with engine.connect() as connection:
        added_table = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='channel_run_claims'"
        ).first()
        states = connection.exec_driver_sql(
            "SELECT state FROM workflow_runs ORDER BY id"
        ).scalars().all()
    assert added_table is None
    assert states == ["POLL_OPEN", "BOOKING_ASSIGNED"]


def test_poll_open_is_not_committed_when_post_fails(factory):
    client = _slack_client()
    client.chat_postMessage.side_effect = RuntimeError("explicit Slack failure")
    engine = WorkflowEngine(factory, client)

    assert engine.start_channel_run(CHANNEL) == m.MSG_POLL_START_FAILED

    with factory() as session:
        run = session.scalar(select(WorkflowRun))
        effect = session.scalar(select(OutboundEffect))
        assert run.state == WorkflowState.FAILED
        assert effect.status == OutboundEffectStatus.FAILED
        assert session.scalar(select(ChannelRunClaim)) is None


def test_poll_post_timeout_is_unknown_and_retains_claim(factory):
    client = _slack_client()
    client.chat_postMessage.side_effect = TimeoutError("response lost")
    engine = WorkflowEngine(factory, client)

    assert engine.start_channel_run(CHANNEL) == m.MSG_POLL_START_FAILED

    with factory() as session:
        claim = session.scalar(select(ChannelRunClaim))
        run = session.get(WorkflowRun, claim.run_id)
        effect = session.scalar(select(OutboundEffect))
        assert run.state == WorkflowState.NEEDS_ATTENTION
        assert run.attention_reason == "POLL_POST_UNKNOWN"
        assert effect.status == OutboundEffectStatus.UNKNOWN


def test_recovery_does_not_repost_an_attempted_pending_poll(factory):
    client = _slack_client()
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).create_claimed_run(
            channel.id,
            state=WorkflowState.POLL_STARTING,
            scheduled_for=datetime.now(ZoneInfo("Asia/Seoul")),
            poll_deadline=datetime.now(ZoneInfo("Asia/Seoul")) + timedelta(hours=24),
            target_member_ids_json=json.dumps(["U1"]),
            initial_effect_type=OutboundEffectType.POLL_OPEN_MESSAGE,
        )
        effect = WorkflowRepository(session).get_effect(
            "workflow_run", str(run.id), OutboundEffectType.POLL_OPEN_MESSAGE
        )
        WorkflowRepository(session).update_effect(
            effect,
            status=OutboundEffectStatus.PENDING,
            increment_attempt=True,
        )

    WorkflowEngine(factory, client).recover_pending_runs()

    client.chat_postMessage.assert_not_called()
    with factory() as session:
        run = WorkflowRepository(session).get_run(run.id)
        assert run.state == WorkflowState.NEEDS_ATTENTION
        assert run.attention_reason == "POLL_POST_UNRESOLVED"


def test_recovery_does_not_repost_an_attempted_pending_result_notice(factory):
    client = _slack_client()
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).create_claimed_run(
            channel.id,
            state=WorkflowState.CLOSE_COMPUTED,
        )
        WorkflowRepository(session).update_run(
            run,
            winning_option_json=json.dumps(
                {"date": "2026-08-14", "counts": {"2026-08-14": 0}}
            ),
        )
        effect = WorkflowRepository(session).ensure_effect(
            aggregate_type="workflow_run",
            aggregate_id=str(run.id),
            effect_type=OutboundEffectType.POLL_RESULT_NOTICE,
            idempotency_key=f"run:{run.id}:poll-result:v1",
        )
        WorkflowRepository(session).update_effect(
            effect,
            status=OutboundEffectStatus.PENDING,
            increment_attempt=True,
        )

    WorkflowEngine(factory, client).recover_pending_runs()

    client.chat_postMessage.assert_not_called()
    with factory() as session:
        run = WorkflowRepository(session).get_run(run.id)
        effect = WorkflowRepository(session).get_effect(
            "workflow_run", str(run.id), OutboundEffectType.POLL_RESULT_NOTICE
        )
        assert run.state == WorkflowState.NEEDS_ATTENTION
        assert run.attention_reason == "POLL_RESULT_UNKNOWN"
        assert effect.status == OutboundEffectStatus.UNKNOWN
        assert effect.error_code == "DELIVERY_UNRESOLVED"


def test_assignee_public_notice_is_not_sent_after_dm_failure(factory, monkeypatch):
    client = _slack_client()

    def post_message(**kwargs):
        if kwargs["channel"] == "U1":
            raise RuntimeError("dm rejected")
        return {"ts": f"{len(client.chat_postMessage.call_args_list)}.1"}

    client.chat_postMessage.side_effect = post_message
    engine = WorkflowEngine(factory, client)
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda values: values[0])

    assert engine.start_channel_run(CHANNEL) is None
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(channel.id)
        run_id = run.id
    engine.close_poll(run_id)

    public_assignment = [
        call
        for call in client.chat_postMessage.call_args_list
        if call.kwargs.get("channel") == CHANNEL
        and "예약 담당입니다" in call.kwargs.get("text", "")
    ]
    assert public_assignment == []
    with factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        dm_effect = WorkflowRepository(session).get_effect(
            "workflow_run", str(run_id), OutboundEffectType.ASSIGNEE_DM
        )
        public_effect = WorkflowRepository(session).get_effect(
            "workflow_run", str(run_id), OutboundEffectType.ASSIGNEE_PUBLIC_NOTICE
        )
        assert run.state == WorkflowState.ASSIGNEE_SELECTED
        assert dm_effect.status == OutboundEffectStatus.FAILED
        assert public_effect.status == OutboundEffectStatus.PENDING


def test_calendar_unknown_is_persisted_and_not_blindly_retried(factory, monkeypatch):
    client = _slack_client()
    calendar_client = MagicMock()
    calendar_client.create_event.return_value = CalendarCreateResult(
        ok=False,
        error="TimeoutError",
        outcome=CalendarOutcome.UNKNOWN,
    )
    engine = WorkflowEngine(factory, client, calendar_client=calendar_client)
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda values: values[0])

    assert engine.start_channel_run(CHANNEL) is None
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run_id = WorkflowRepository(session).get_open_run(channel.id).id
    engine.close_poll(run_id)
    engine.recover_pending_runs()

    assert calendar_client.create_event.call_count == 1
    with factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        effect = WorkflowRepository(session).get_effect(
            "workflow_run", str(run_id), OutboundEffectType.CALENDAR_CREATE
        )
        assert run.calendar_outcome == CalendarOutcome.UNKNOWN
        assert effect.status == OutboundEffectStatus.UNKNOWN


def test_calendar_attempted_pending_is_unknown_without_duplicate_create(factory):
    client = _slack_client()
    calendar_client = MagicMock()
    engine = WorkflowEngine(factory, client, calendar_client=calendar_client)
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).create_claimed_run(
            channel.id,
            state=WorkflowState.ASSIGNEE_SELECTED,
        )
        WorkflowRepository(session).update_run(
            run,
            winning_option_json=json.dumps(
                {"date": "2026-08-14", "counts": {"2026-08-14": 0}}
            ),
            assignee_user_id="U1",
            calendar_operation_id=f"preexisting-{run.id}",
        )
        effect = WorkflowRepository(session).ensure_effect(
            aggregate_type="workflow_run",
            aggregate_id=str(run.id),
            effect_type=OutboundEffectType.CALENDAR_CREATE,
            idempotency_key=f"calendar:preexisting-{run.id}:v1",
        )
        WorkflowRepository(session).update_effect(
            effect,
            status=OutboundEffectStatus.PENDING,
            increment_attempt=True,
        )

    outcome, calendar_url, _operation_id = engine._calendar_details(run.id, CHANNEL)

    assert outcome == CalendarOutcome.UNKNOWN
    assert calendar_url is None
    calendar_client.create_event.assert_not_called()
    with factory() as session:
        run = WorkflowRepository(session).get_run(run.id)
        effect = WorkflowRepository(session).get_effect(
            "workflow_run", str(run.id), OutboundEffectType.CALENDAR_CREATE
        )
        assert run.calendar_outcome == CalendarOutcome.UNKNOWN
        assert effect.status == OutboundEffectStatus.UNKNOWN
        assert effect.error_code == "CALENDAR_DELIVERY_UNRESOLVED"


def test_no_assignee_retains_claim_and_resumes_with_same_winner(factory, monkeypatch):
    client = _slack_client()
    client.conversations_members.return_value = {"members": []}
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        channel.poll_target_ids_json = json.dumps([])
        channel.channel_member_ids_json = json.dumps([])
        session.commit()
    engine = WorkflowEngine(factory, client)
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda values: values[0])

    assert engine.start_channel_run(CHANNEL) is None
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run_id = WorkflowRepository(session).get_open_run(channel.id).id
    engine.close_poll(run_id)

    with factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        claim = session.get(ChannelRunClaim, run.channel_id)
        winner_before = run.winning_option_json
        assert run.state == WorkflowState.NEEDS_ATTENTION
        assert run.attention_reason == "NO_ASSIGNEE_AVAILABLE"
        assert run.result_code == "ASSIGNEE_UNAVAILABLE"
        assert claim.run_id == run_id
        channel = session.get(Channel, run.channel_id)
        channel.poll_target_ids_json = json.dumps(["U1"])
        session.commit()

    client.conversations_members.return_value = {"members": ["U1"]}
    engine.resume_channel(CHANNEL)

    with factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.ASSIGNEE_SELECTED
        assert run.assignee_user_id == "U1"
        assert run.winning_option_json == winner_before


def test_no_assignee_explicit_cancel_releases_claim(factory, monkeypatch):
    client = _slack_client()
    client.conversations_members.return_value = {"members": []}
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        channel.poll_target_ids_json = json.dumps([])
        channel.channel_member_ids_json = json.dumps([])
        session.commit()
    engine = WorkflowEngine(factory, client)
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda values: values[0])

    assert engine.start_channel_run(CHANNEL) is None
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run_id = WorkflowRepository(session).get_open_run(channel.id).id
    engine.close_poll(run_id)
    assert engine.cancel_current_run(CHANNEL) == m.MSG_RUN_CANCELLED

    with factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.state == WorkflowState.DONE
        assert run.terminal_reason == "CANCELLED_NO_ASSIGNEE"
        assert session.get(ChannelRunClaim, run.channel_id) is None


def test_restart_closes_overdue_poll_without_extending_deadline(factory, monkeypatch):
    client = _slack_client()
    engine = WorkflowEngine(factory, client)
    monkeypatch.setattr("app.workflow.engine.random.choice", lambda values: values[0])
    assert engine.start_channel_run(CHANNEL) is None

    original_deadline = datetime.now(ZoneInfo("Asia/Seoul")) - timedelta(minutes=5)
    with factory() as session:
        channel = ChannelRepository(session).get_by_channel_id(CHANNEL)
        run = WorkflowRepository(session).get_open_run(channel.id)
        run_id = run.id
        WorkflowRepository(session).update_run(run, poll_deadline=original_deadline)

    engine.recover_pending_runs()

    with factory() as session:
        run = WorkflowRepository(session).get_run(run_id)
        assert run.poll_deadline == original_deadline.replace(tzinfo=None)
        assert run.state == WorkflowState.ASSIGNEE_SELECTED


def test_startup_fails_closed_before_non_sqlite_schema_write(monkeypatch):
    fake_engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.setattr("app.db.models.get_engine", lambda: fake_engine)

    with pytest.raises(RuntimeError, match="Only SQLite is supported"):
        init_db()


def test_protected_engine_slack_calls_use_delivery_contract():
    source = Path("app/workflow/engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"chat_postMessage", "chat_postEphemeral", "chat_update"}
    ]
    assert direct_calls == []
