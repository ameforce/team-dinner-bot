# -*- coding: utf-8 -*-
"""Team dinner Slack bot (Socket Mode)."""

from __future__ import annotations

import atexit
import logging
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.config import settings
from app.db.models import init_db
from app.handlers.actions import register_action_handlers
from app.handlers.commands import register_command_handlers
from app.handlers.events import register_event_handlers
from app.scheduler.runner import JobScheduler
from app.workflow.engine import WorkflowEngine

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_job_scheduler: JobScheduler | None = None


def _runtime_parent_dirs(database_url: str, lock_file_path: str) -> set[Path]:
    dirs: set[Path] = set()
    parsed_url = make_url(database_url)

    if parsed_url.drivername.startswith("sqlite"):
        database = parsed_url.database
        if database and database != ":memory:":
            db_parent = Path(database).parent
            if str(db_parent) not in ("", "."):
                dirs.add(db_parent)

    lock_parent = Path(lock_file_path).parent
    if str(lock_parent) not in ("", "."):
        dirs.add(lock_parent)
    return dirs


def _ensure_runtime_dirs() -> None:
    for parent in _runtime_parent_dirs(settings.database_url, settings.lock_file_path):
        parent.mkdir(parents=True, exist_ok=True)


def create_app() -> App:
    global _job_scheduler
    app = App(token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret)

    @app.middleware
    def log_incoming_events(body, next, logger):
        event = body.get("event") or {}
        if event.get("type"):
            logger.info("slack event: %s", event.get("type"))
        next()

    session_factory = init_db()
    engine = WorkflowEngine(session_factory, app.client)
    _job_scheduler = JobScheduler(session_factory, engine)
    register_event_handlers(app, session_factory, engine, _job_scheduler)
    register_command_handlers(app, session_factory, engine, _job_scheduler)
    register_action_handlers(app, session_factory, engine)
    _job_scheduler.start()
    atexit.register(_job_scheduler.shutdown)
    return app


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_singleton_lock() -> None:
    """Prevent multiple Socket Mode workers (stale in-memory strings, duplicate handlers)."""
    lock_path = Path(settings.lock_file_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = 0
        if _pid_alive(old_pid):
            logger.error(
                "Another %s instance is already running (pid %s, lock %s)",
                settings.app_slug,
                old_pid,
                lock_path,
            )
            sys.exit(1)
        lock_path.unlink(missing_ok=True)

    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    def _release() -> None:
        try:
            if lock_path.exists() and lock_path.read_text(encoding="utf-8").strip() == str(
                os.getpid()
            ):
                lock_path.unlink()
        except OSError:
            pass

    atexit.register(_release)


def main() -> None:
    _ensure_runtime_dirs()
    _acquire_singleton_lock()
    app = create_app()
    handler = SocketModeHandler(app, settings.slack_app_token)
    logger.info("%s starting (Socket Mode)...", settings.app_slug)
    handler.start()


if __name__ == "__main__":
    main()
