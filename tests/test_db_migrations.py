# -*- coding: utf-8 -*-
from __future__ import annotations

from sqlalchemy import create_engine, text

from app.db.models import _migrate_existing_sqlite


def test_sqlite_migration_adds_selection_audit_json_to_existing_workflow_runs(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                create table channels (
                    id integer primary key,
                    team_id varchar(32),
                    channel_id varchar(32),
                    enabled boolean,
                    schedule_json text,
                    poll_duration_hours integer,
                    tz varchar(64),
                    booking_url_template text,
                    created_at datetime
                )
                """
            )
        )
        conn.execute(
            text(
                """
                create table workflow_runs (
                    id integer primary key,
                    channel_id integer,
                    state varchar(32)
                )
                """
            )
        )

    _migrate_existing_sqlite(engine)

    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(workflow_runs)")).mappings().all()
    columns = {row["name"] for row in rows}
    assert "selection_audit_json" in columns
