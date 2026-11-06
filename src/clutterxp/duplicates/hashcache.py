"""SQLite-based hash cache for duplicate detection.

Persists partial and full SHA-256 hashes keyed by (dev, ino, size, mtime_ns).
Thread-safe with thread-local connections and WAL mode.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class HashCache:
    """Thread-safe SQLite hash cache for file duplicate detection."""

    def __init__(self, cache_path: Path) -> None:
        """Creates cache.db at cache_path with 0600 perms. One connection per thread."""
        self._cache_path = cache_path.resolve()
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._all_conns: set[sqlite3.Connection] = set()

        # Ensure parent directory exists with 0700 permissions
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self._cache_path.parent.chmod(0o700)

        # Create file with 0600 permissions if it doesn't exist
        if not self._cache_path.exists():
            fd = os.open(self._cache_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
        else:
            with contextlib.suppress(OSError):
                self._cache_path.chmod(0o600)

        # Initialize schema
        conn = self._get_conn()
        with self._write_lock:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hashes (
                    dev          INTEGER NOT NULL,
                    ino          INTEGER NOT NULL,
                    size         INTEGER NOT NULL,
                    mtime_ns     INTEGER NOT NULL,
                    partial_hash TEXT,
                    full_hash    TEXT,
                    hashed_at    TEXT NOT NULL,
                    PRIMARY KEY (dev, ino, size, mtime_ns)
                );
                """
            )
            conn.commit()

        # Enforce 0600 permissions after database initialization
        with contextlib.suppress(OSError):
            self._cache_path.chmod(0o600)

    def _get_conn(self) -> sqlite3.Connection:
        """Get or initialize thread-local sqlite3 connection."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._cache_path, timeout=30.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn = conn
            with self._write_lock:
                self._all_conns.add(conn)
        return conn

    def get(self, dev: int, ino: int, size: int, mtime_ns: int) -> tuple[str | None, str | None]:
        """Returns (partial_hash, full_hash) or (None, None) if not cached."""
        conn = self._get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT partial_hash, full_hash
            FROM hashes
            WHERE dev = ? AND ino = ? AND size = ? AND mtime_ns = ?
            """,
            (dev, ino, size, mtime_ns),
        )
        row = cur.fetchone()
        if row is None:
            return (None, None)
        return (row[0], row[1])

    def put(
        self,
        dev: int,
        ino: int,
        size: int,
        mtime_ns: int,
        partial_hash: str | None,
        full_hash: str | None,
    ) -> None:
        """Insert or update cached hashes for a file."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO hashes (dev, ino, size, mtime_ns, partial_hash, full_hash, hashed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dev, ino, size, mtime_ns) DO UPDATE SET
                    partial_hash = COALESCE(excluded.partial_hash, hashes.partial_hash),
                    full_hash = COALESCE(excluded.full_hash, hashes.full_hash),
                    hashed_at = excluded.hashed_at
                """,
                (dev, ino, size, mtime_ns, partial_hash, full_hash, now_iso),
            )
            conn.commit()

    def prune(self, older_than_days: int = 90) -> int:
        """Delete rows not seen for N days. Returns count."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._write_lock:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM hashes WHERE hashed_at < ?", (cutoff_iso,))
            count = cur.rowcount
            conn.commit()
            return max(0, count)

    def clear(self) -> None:
        """Delete all rows."""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM hashes")
            conn.commit()

    def close(self) -> None:
        """Close all connections opened by threads."""
        with self._write_lock:
            for conn in list(self._all_conns):
                try:
                    conn.close()
                except Exception as err:
                    logger.debug("Error closing connection: %s", err)
            self._all_conns.clear()
            self._local.conn = None
