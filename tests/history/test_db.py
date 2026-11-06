"""Tests for database initialization, migrations, permissions, and corruption recovery."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from clutterxp.history.db import (
    CURRENT_SCHEMA_VERSION,
    connect,
    handle_corrupt_db,
    integrity_check,
    run_migrations,
)


def test_schema_creation_and_idempotent_reopen(tmp_path: Path) -> None:
    """Test initial database schema creation and that reopening is idempotent."""
    db_path = tmp_path / "history.db"

    conn1 = connect(db_path)
    cur = conn1.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = {row[0] for row in cur.fetchall()}
    assert {"batches", "operations", "events"}.issubset(tables)

    ver_row = conn1.execute("PRAGMA user_version;").fetchone()
    assert ver_row[0] == CURRENT_SCHEMA_VERSION
    conn1.close()

    # Reopening should not error and schema stays intact
    conn2 = connect(db_path)
    cur2 = conn2.execute("PRAGMA user_version;")
    assert cur2.fetchone()[0] == CURRENT_SCHEMA_VERSION
    conn2.close()


def test_pragmas_applied(tmp_path: Path) -> None:
    """Verify that required SQLite pragmas are active."""
    db_path = tmp_path / "pragmas.db"
    conn = connect(db_path)

    journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert str(journal_mode).lower() == "wal"

    # SQLite returns integer 2 for FULL synchronous mode
    synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
    assert synchronous == 2

    foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert foreign_keys == 1

    busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert busy_timeout == 5000

    conn.close()


def test_migration_runner_with_fake_v2_step(tmp_path: Path) -> None:
    """Verify migration runner applies pending migrations in individual transactions."""
    db_path = tmp_path / "migrate.db"
    conn = connect(db_path)

    # Currently at version 1
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 1

    def fake_v2(c: sqlite3.Connection) -> None:
        c.execute("ALTER TABLE operations ADD COLUMN notes TEXT;")

    run_migrations(conn, custom_migrations={2: fake_v2})

    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 2

    # Check that new column exists by querying table_info
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(operations);").fetchall()]
    assert "notes" in columns

    # Running migrations again should be idempotent
    run_migrations(conn, custom_migrations={2: fake_v2})
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 2

    conn.close()


def test_A40_corrupt_db_renamed_not_deleted(tmp_path: Path) -> None:
    """A40: A corrupt DB is renamed to history.db.corrupt-<timestamp>,

    never deleted, and a fresh DB is created.
    """
    db_path = tmp_path / "history.db"
    garbage_bytes = b"CORRUPTED_DATABASE_GARBAGE_BYTES_12345"
    db_path.write_bytes(garbage_bytes)

    # Opening corrupt DB must trigger corruption handler
    conn = connect(db_path)
    assert integrity_check(conn) is True

    # Check that original corrupt file was renamed, not deleted
    corrupt_files = list(tmp_path.glob("history.db.corrupt-*"))
    assert len(corrupt_files) == 1
    corrupt_file = corrupt_files[0]
    assert corrupt_file.exists()
    assert corrupt_file.read_bytes() == garbage_bytes

    # Check that the fresh DB is fully functional with schema version 1
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    }
    assert {"batches", "operations", "events"}.issubset(tables)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 1

    conn.close()


def test_handle_corrupt_db_explicit_call(tmp_path: Path) -> None:
    """Test calling handle_corrupt_db explicitly."""
    db_path = tmp_path / "corrupt_explicit.db"
    db_path.write_bytes(b"bad content")

    conn = handle_corrupt_db(db_path)
    assert integrity_check(conn) is True

    corrupt_files = list(tmp_path.glob("corrupt_explicit.db.corrupt-*"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_bytes() == b"bad content"

    conn.close()


def test_S12_db_permissions_0600(tmp_path: Path) -> None:
    """S12: All state files are created with 0600 (parent dirs 0700)."""
    db_dir = tmp_path / "state_subdir"
    db_path = db_dir / "history.db"

    conn = connect(db_path)
    conn.close()

    dir_mode = db_dir.stat().st_mode & 0o777
    assert dir_mode == 0o700, f"Expected dir mode 0700, got {oct(dir_mode)}"

    file_mode = db_path.stat().st_mode & 0o777
    assert file_mode == 0o600, f"Expected file mode 0600, got {oct(file_mode)}"


def test_integrity_check_detects_bad_db(tmp_path: Path) -> None:
    """integrity_check returns False on an invalid SQLite connection or corrupt file."""
    db_path = tmp_path / "corrupt_check.db"
    db_path.write_bytes(b"not sqlite")
    raw_conn = sqlite3.connect(str(db_path))
    assert integrity_check(raw_conn) is False
    raw_conn.close()


def test_migration_runner_rollback_on_failure(tmp_path: Path) -> None:
    """Verify migration transaction rolls back on failure."""
    db_path = tmp_path / "migration_fail.db"
    conn = connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 1

    def failing_v2(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE will_rollback (id INT);")
        msg = "Migration deliberate failure"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="Migration deliberate failure"):
        run_migrations(conn, custom_migrations={2: failing_v2})

    # user_version must still be 1
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 1

    # Table must not exist
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='will_rollback';"
        ).fetchall()
    ]
    assert len(tables) == 0
    conn.close()


def test_handle_corrupt_db_wal_shm_and_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify handle_corrupt_db renames auxiliary files and handles naming collisions."""
    db_path = tmp_path / "test_wal.db"
    db_path.write_bytes(b"bad db")
    wal_path = Path(f"{db_path}-wal")
    wal_path.write_bytes(b"bad wal")
    shm_path = Path(f"{db_path}-shm")
    shm_path.write_bytes(b"bad shm")

    # Force fixed timestamp to test collision counter loop
    fixed_ts = "20261106-120000"
    existing_corrupt = tmp_path / f"test_wal.db.corrupt-{fixed_ts}"
    existing_corrupt.write_bytes(b"pre-existing")

    from datetime import datetime, timezone

    class FixedDatetime:
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("clutterxp.history.db.datetime", FixedDatetime)

    conn = handle_corrupt_db(db_path)
    conn.close()

    # The collision should have caused test_wal.db.corrupt-20261106-120000-1
    collision_file = tmp_path / f"test_wal.db.corrupt-{fixed_ts}-1"
    assert collision_file.exists()
    assert collision_file.read_bytes() == b"bad db"

    # wal and shm files should also be renamed
    assert Path(f"{collision_file}-wal").exists()
    assert Path(f"{collision_file}-shm").exists()


def test_connect_memory_db() -> None:
    """Verify connect works on :memory: databases."""
    conn = connect(Path(":memory:"))
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == CURRENT_SCHEMA_VERSION
    conn.close()


def test_connect_wal_retry_on_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify connect retries when PRAGMA journal_mode encounters transient lock."""
    db_path = tmp_path / "wal_retry.db"
    orig_connect = sqlite3.connect

    first = True

    class MockConnection(sqlite3.Connection):
        def execute(self, sql: str, *args_e: Any, **kwargs_e: Any) -> Any:
            nonlocal first
            if "PRAGMA journal_mode = WAL;" in sql and first:
                first = False
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args_e, **kwargs_e)

    def mock_sqlite3_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs["factory"] = MockConnection
        conn_res: sqlite3.Connection = orig_connect(*args, **kwargs)
        return conn_res

    monkeypatch.setattr(sqlite3, "connect", mock_sqlite3_connect)
    conn = connect(db_path)
    assert conn is not None
    conn.close()
