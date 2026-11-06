"""SQLite connection, schema migration, permissions, and corruption recovery."""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
import warnings
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("clutterxp.history.db")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
CURRENT_SCHEMA_VERSION = 1

MigrationFn = Callable[[sqlite3.Connection], None]


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Version 1 migration: create initial tables and indexes from schema.sql."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    for stmt in schema_sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


MIGRATIONS: dict[int, MigrationFn] = {
    1: _migrate_v1,
}


def integrity_check(conn: sqlite3.Connection) -> bool:
    """Run PRAGMA integrity_check. Returns True if ok."""
    try:
        cur = conn.execute("PRAGMA integrity_check;")
        rows = [row[0] for row in cur.fetchall()]
        return len(rows) == 1 and str(rows[0]).strip().lower() == "ok"
    except sqlite3.DatabaseError:
        return False


def run_migrations(
    conn: sqlite3.Connection,
    custom_migrations: dict[int, MigrationFn] | None = None,
) -> None:
    """Apply any pending migrations in order.

    Each migration runs in its own transaction.
    Current schema is version 1.
    """
    migrations = dict(MIGRATIONS)
    if custom_migrations:
        migrations.update(custom_migrations)

    for version in sorted(migrations.keys()):
        conn.execute("BEGIN IMMEDIATE;")
        try:
            cur = conn.execute("PRAGMA user_version;")
            row = cur.fetchone()
            current_version = row[0] if row else 0
            if version > current_version:
                migrations[version](conn)
                conn.execute(f"PRAGMA user_version = {version};")
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise


def handle_corrupt_db(db_path: Path) -> sqlite3.Connection:
    """Rename to history.db.corrupt-<timestamp>, create fresh DB, warn."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    corrupt_path = db_path.parent / f"{db_path.name}.corrupt-{ts}"
    counter = 1
    while corrupt_path.exists():
        corrupt_path = db_path.parent / f"{db_path.name}.corrupt-{ts}-{counter}"
        counter += 1

    msg = f"Database {db_path} is corrupt. Renaming to {corrupt_path} and creating fresh database."
    warnings.warn(msg, RuntimeWarning, stacklevel=2)
    logger.warning(msg)

    if db_path.exists():
        db_path.rename(corrupt_path)

    for ext in ("-wal", "-shm"):
        aux = Path(f"{db_path}{ext}")
        if aux.exists():
            aux.rename(Path(f"{corrupt_path}{ext}"))

    return connect(db_path)


def is_corrupt_error(err: Exception) -> bool:
    """Return True if an exception indicates SQLite database corruption."""
    msg = str(err).lower()
    return any(
        s in msg
        for s in (
            "file is not a database",
            "file is encrypted",
            "database disk image is malformed",
            "malformed database schema",
            "corrupt",
        )
    )


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the DB with correct pragmas and permissions.

    File created 0600. Schema applied if new.
    Returns connection with check_same_thread=True.
    Runs migrations if user_version < current.
    """
    is_memory = str(db_path) == ":memory:"
    resolved_path = Path(db_path)

    if not is_memory:
        resolved_path = resolved_path.resolve()
        resolved_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            resolved_path.parent.chmod(0o700)
        if not resolved_path.exists():
            with contextlib.suppress(FileExistsError):
                fd = os.open(resolved_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
        with contextlib.suppress(OSError):
            resolved_path.chmod(0o600)

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            str(resolved_path) if not is_memory else ":memory:",
            timeout=10.0,
            check_same_thread=True,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row

        for attempt in range(20):
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                break
            except sqlite3.OperationalError as e:
                is_lock = "locked" in str(e).lower() or "busy" in str(e).lower()
                if is_lock and attempt < 19:
                    time.sleep(0.02 * (attempt + 1))
                else:
                    raise

        conn.execute("PRAGMA synchronous = FULL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")

        run_migrations(conn)

        if not is_memory:
            with contextlib.suppress(OSError):
                resolved_path.chmod(0o600)

        return conn
    except Exception as err:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        if not is_memory and is_corrupt_error(err):
            return handle_corrupt_db(resolved_path)
        raise
