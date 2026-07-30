from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _default_timezone() -> str:
    return settings.default_timezone


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(String(32), index=True)
    channel_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    automatic_execution_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_duration_hours: Mapped[int] = mapped_column(Integer, default=24)
    tz: Mapped[str] = mapped_column(String(64), default=_default_timezone)
    booking_url_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_target_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_invitees_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_member_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    runs: Mapped[list[WorkflowRun]] = relationship(back_populates="channel")


class PollVote(Base):
    __tablename__ = "poll_votes"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    slack_user_id: Mapped[str] = mapped_column(String(32), index=True)
    date_iso: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    state: Mapped[str] = mapped_column(String(32), default="POLL_STARTING")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    poll_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    poll_semantics: Mapped[str | None] = mapped_column(String(32), default="unavailable", nullable=True)
    winning_option_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_member_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thread_ts: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attention_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calendar_operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calendar_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    calendar_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_html_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    channel: Mapped[Channel] = relationship(back_populates="runs")


class ChannelRunClaim(Base):
    __tablename__ = "channel_run_claims"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), primary_key=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id"), unique=True, nullable=False
    )
    claim_version: Mapped[int] = mapped_column(Integer, default=1)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OutboundEffect(Base):
    __tablename__ = "outbound_effects"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbound_effect_idempotency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(32), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    effect_type: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    remote_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SchemaMetadata(Base):
    __tablename__ = "schema_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(64), nullable=False)


class UserEmailMap(Base):
    __tablename__ = "user_email_map"

    slack_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), default="slack_profile")


class AssigneeHistory(Base):
    __tablename__ = "assignee_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id"), unique=True, nullable=True
    )
    user_id: Mapped[str] = mapped_column(String(32))
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def get_engine():
    return create_engine(settings.database_url, future=True)


def init_db() -> sessionmaker:
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "Unsupported DATABASE_URL dialect for v2 migration: "
            f"{engine.dialect.name}. Only SQLite is supported."
        )
    _preflight_existing_sqlite(engine)
    Base.metadata.create_all(engine)
    _migrate_existing_sqlite(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _preflight_existing_sqlite(engine) -> None:
    with engine.connect() as conn:
        workflow_runs_exists = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='workflow_runs'"
            )
        ).first()
        if not workflow_runs_exists:
            return
        duplicate = conn.execute(
            text(
                """
                SELECT channel_id, COUNT(*) AS count
                FROM workflow_runs
                WHERE state IN (
                    'POLL_STARTING', 'POLL_OPEN', 'REMIND_POSTED',
                    'POLL_CLOSED', 'BOOKING_ASSIGNED',
                    'CLOSE_COMPUTED', 'ASSIGNEE_SELECTED', 'NEEDS_ATTENTION'
                )
                GROUP BY channel_id
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            )
        ).mappings().first()
    if duplicate:
        raise RuntimeError(
            "SQLite v2 migration requires manual reconciliation: "
            f"channel {duplicate['channel_id']} has {duplicate['count']} active runs"
        )


def _migrate_existing_sqlite(engine) -> None:
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "Unsupported database dialect for v2 migration: "
            f"{engine.dialect.name}. Only SQLite is supported."
        )
    _preflight_existing_sqlite(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        channel_rows = conn.execute(text("PRAGMA table_info(channels)")).mappings().all()
        channel_columns = {row["name"] for row in channel_rows}
        for column in (
            "automatic_execution_enabled",
            "poll_target_ids_json",
            "calendar_invitees_json",
            "channel_member_ids_json",
            "settings_revision",
        ):
            if column not in channel_columns:
                if column == "automatic_execution_enabled":
                    conn.execute(
                        text("ALTER TABLE channels ADD COLUMN automatic_execution_enabled BOOLEAN DEFAULT 1")
                    )
                elif column == "settings_revision":
                    conn.execute(
                        text("ALTER TABLE channels ADD COLUMN settings_revision INTEGER DEFAULT 1")
                    )
                else:
                    conn.execute(text(f"ALTER TABLE channels ADD COLUMN {column} TEXT"))

        rows = conn.execute(text("PRAGMA table_info(workflow_runs)")).mappings().all()
        columns = {row["name"] for row in rows}
        workflow_columns = {
            "target_member_ids_json": "TEXT",
            "poll_semantics": "VARCHAR(32)",
            "selection_audit_json": "TEXT",
            "scheduled_for": "DATETIME",
            "poll_deadline": "DATETIME",
            "winning_option_json": "TEXT",
            "assignee_user_id": "VARCHAR(32)",
            "thread_ts": "VARCHAR(32)",
            "terminal_reason": "VARCHAR(64)",
            "attention_reason": "VARCHAR(64)",
            "result_code": "VARCHAR(64)",
            "calendar_operation_id": "VARCHAR(64)",
            "calendar_outcome": "VARCHAR(32)",
            "calendar_event_id": "VARCHAR(255)",
            "calendar_html_link": "TEXT",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        }
        for column, sql_type in workflow_columns.items():
            if column not in columns:
                conn.execute(
                    text(f"ALTER TABLE workflow_runs ADD COLUMN {column} {sql_type}")
                )

        history_rows = conn.execute(
            text("PRAGMA table_info(assignee_history)")
        ).mappings().all()
        history_columns = {row["name"] for row in history_rows}
        if "run_id" not in history_columns:
            conn.execute(
                text("ALTER TABLE assignee_history ADD COLUMN run_id INTEGER")
            )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_assignee_history_run_id "
                "ON assignee_history(run_id) WHERE run_id IS NOT NULL"
            )
        )

        conn.execute(
            text(
                "UPDATE workflow_runs SET state='CLOSE_COMPUTED' "
                "WHERE state='POLL_CLOSED'"
            )
        )
        conn.execute(
            text(
                "UPDATE workflow_runs SET state='ASSIGNEE_SELECTED' "
                "WHERE state='BOOKING_ASSIGNED'"
            )
        )
        conn.execute(
            text(
                "UPDATE workflow_runs SET state='POLL_OPEN' "
                "WHERE state='REMIND_POSTED'"
            )
        )
        conn.execute(
            text(
                """
                INSERT OR IGNORE INTO channel_run_claims
                    (channel_id, run_id, claim_version, claimed_at)
                SELECT channel_id, id, 1, COALESCE(created_at, CURRENT_TIMESTAMP)
                FROM workflow_runs
                WHERE state IN (
                    'POLL_STARTING', 'POLL_OPEN', 'REMIND_POSTED',
                    'CLOSE_COMPUTED', 'ASSIGNEE_SELECTED', 'NEEDS_ATTENTION'
                )
                """
            )
        )
        active_rows = conn.execute(
            text(
                """
                SELECT id, state, thread_ts
                FROM workflow_runs
                WHERE state IN (
                    'POLL_STARTING', 'POLL_OPEN', 'REMIND_POSTED',
                    'CLOSE_COMPUTED', 'ASSIGNEE_SELECTED'
                )
                """
            )
        ).mappings().all()
        for row in active_rows:
            run_id = str(row["id"])
            state = row["state"]
            if state in {"POLL_STARTING", "POLL_OPEN", "REMIND_POSTED"}:
                status = "SENT" if row["thread_ts"] else "UNKNOWN"
                conn.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO outbound_effects (
                            aggregate_type, aggregate_id, effect_type,
                            idempotency_key, status, remote_ref, attempt_count,
                            payload_version, created_at, updated_at
                        ) VALUES (
                            'workflow_run', :run_id, 'POLL_OPEN_MESSAGE',
                            :key, :status, :remote_ref, 0, 1,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "run_id": run_id,
                        "key": f"run:{run_id}:poll-open:v1",
                        "status": status,
                        "remote_ref": row["thread_ts"],
                    },
                )
            if state in {"CLOSE_COMPUTED", "ASSIGNEE_SELECTED"}:
                conn.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO outbound_effects (
                            aggregate_type, aggregate_id, effect_type,
                            idempotency_key, status, attempt_count,
                            payload_version, created_at, updated_at
                        ) VALUES (
                            'workflow_run', :run_id, 'POLL_RESULT_NOTICE',
                            :key, 'UNKNOWN', 0, 1,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {"run_id": run_id, "key": f"run:{run_id}:poll-result:v1"},
                )
            if state == "ASSIGNEE_SELECTED":
                for effect_type, suffix in (
                    ("ASSIGNEE_DM", "assignee-dm"),
                    ("ASSIGNEE_PUBLIC_NOTICE", "assignee-public"),
                ):
                    conn.execute(
                        text(
                            """
                            INSERT OR IGNORE INTO outbound_effects (
                                aggregate_type, aggregate_id, effect_type,
                                idempotency_key, status, attempt_count,
                                payload_version, created_at, updated_at
                            ) VALUES (
                                'workflow_run', :run_id, :effect_type,
                                :key, 'UNKNOWN', 0, 1,
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            """
                        ),
                        {
                            "run_id": run_id,
                            "effect_type": effect_type,
                            "key": f"run:{run_id}:{suffix}:v1",
                        },
                    )

        conn.execute(
            text(
                """
                UPDATE workflow_runs
                SET state='NEEDS_ATTENTION',
                    attention_reason='POLL_POST_UNKNOWN',
                    result_code='POLL_POST_UNKNOWN'
                WHERE state IN ('POLL_OPEN', 'REMIND_POSTED')
                  AND thread_ts IS NULL
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE workflow_runs
                SET state='NEEDS_ATTENTION',
                    attention_reason='LEGACY_EFFECT_STATUS_UNKNOWN',
                    result_code='LEGACY_EFFECT_STATUS_UNKNOWN'
                WHERE state IN ('CLOSE_COMPUTED', 'ASSIGNEE_SELECTED')
                """
            )
        )

        conn.execute(
            text(
                """
                INSERT INTO schema_metadata(key, value)
                VALUES ('schema_version', '2')
                ON CONFLICT(key) DO UPDATE SET value='2'
                """
            )
        )


def schedule_to_json(spec_dict: dict) -> str:
    return json.dumps(spec_dict, ensure_ascii=False)
