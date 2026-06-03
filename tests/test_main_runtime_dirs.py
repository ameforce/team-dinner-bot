# -*- coding: utf-8 -*-
from __future__ import annotations


def test_runtime_parent_dirs_follow_configured_sqlite_and_lock_paths(tmp_path):
    from app.main import _runtime_parent_dirs

    db_path = tmp_path / "custom-db" / "team-dinner.sqlite"
    lock_path = tmp_path / "locks" / "team-dinner.lock"

    assert _runtime_parent_dirs(
        f"sqlite:///{db_path.as_posix()}",
        str(lock_path),
    ) == {db_path.parent, lock_path.parent}


def test_runtime_parent_dirs_ignore_non_file_database_paths(tmp_path):
    from app.main import _runtime_parent_dirs

    lock_path = tmp_path / "locks" / "team-dinner.lock"

    assert _runtime_parent_dirs("sqlite:///:memory:", str(lock_path)) == {lock_path.parent}
    assert _runtime_parent_dirs("postgresql://user@example/db", str(lock_path)) == {
        lock_path.parent
    }
