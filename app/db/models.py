from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, text
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
    schedule_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_duration_hours: Mapped[int] = mapped_column(Integer, default=24)
    tz: Mapped[str] = mapped_column(String(64), default=_default_timezone)
    booking_url_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_target_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_invitees_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_member_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    state: Mapped[str] = mapped_column(String(32), default="IDLE")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    poll_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    poll_semantics: Mapped[str | None] = mapped_column(String(32), default="unavailable", nullable=True)
    winning_option_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_member_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thread_ts: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    channel: Mapped[Channel] = relationship(back_populates="runs")


class UserEmailMap(Base):
    __tablename__ = "user_email_map"

    slack_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), default="slack_profile")


class AssigneeHistory(Base):
    __tablename__ = "assignee_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(32))
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def get_engine():
    return create_engine(settings.database_url, future=True)


def init_db() -> sessionmaker:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_existing_sqlite(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _migrate_existing_sqlite(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        channel_rows = conn.execute(text("PRAGMA table_info(channels)")).mappings().all()
        channel_columns = {row["name"] for row in channel_rows}
        for column in (
            "poll_target_ids_json",
            "calendar_invitees_json",
            "channel_member_ids_json",
        ):
            if column not in channel_columns:
                conn.execute(text(f"ALTER TABLE channels ADD COLUMN {column} TEXT"))

        rows = conn.execute(text("PRAGMA table_info(workflow_runs)")).mappings().all()
        columns = {row["name"] for row in rows}
        if "target_member_ids_json" not in columns:
            conn.execute(text("ALTER TABLE workflow_runs ADD COLUMN target_member_ids_json TEXT"))
        if "poll_semantics" not in columns:
            conn.execute(text("ALTER TABLE workflow_runs ADD COLUMN poll_semantics VARCHAR(32)"))
        if "selection_audit_json" not in columns:
            conn.execute(text("ALTER TABLE workflow_runs ADD COLUMN selection_audit_json TEXT"))


def schedule_to_json(spec_dict: dict) -> str:
    return json.dumps(spec_dict, ensure_ascii=False)
