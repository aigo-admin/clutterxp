"""History service: journal-first operations, queries, prune, and events."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Generator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from clutterxp.history import pathcodec
from clutterxp.history.db import connect
from clutterxp.models import (
    FileInfo,
    MoveResult,
    MoveStrategy,
    OperationRecord,
    OpStatus,
)
from clutterxp.units import parse_duration_seconds


def _default_clock() -> str:
    """Return current UTC time formatted as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    """Parse ISO-8601 string into datetime in UTC."""
    normalized = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


@contextlib.contextmanager
def _write_tx(conn: sqlite3.Connection, max_retries: int = 30) -> Generator[None, None, None]:
    """Context manager for immediate write transactions with automatic backoff retry on locks."""
    for attempt in range(max_retries):
        try:
            conn.execute("BEGIN IMMEDIATE;")
            break
        except sqlite3.OperationalError as e:
            is_locked = "locked" in str(e).lower() or "busy" in str(e).lower()
            if is_locked and attempt < max_retries - 1:
                time.sleep(0.01 * (attempt + 1))
            else:
                raise
    try:
        yield
        for attempt in range(max_retries):
            try:
                conn.execute("COMMIT;")
                break
            except sqlite3.OperationalError as e:
                is_locked = "locked" in str(e).lower() or "busy" in str(e).lower()
                if is_locked and attempt < max_retries - 1:
                    time.sleep(0.01 * (attempt + 1))
                else:
                    raise
    except Exception:
        with contextlib.suppress(Exception):
            conn.execute("ROLLBACK;")
        raise


def op_to_dict(op: OperationRecord) -> dict[str, Any]:
    """Convert an OperationRecord into a JSON-serializable dictionary."""
    return {
        "id": op.id,
        "batch_id": op.batch_id,
        "status": op.status.value,
        "src_path": str(op.src_path),
        "dst_path": str(op.dst_path) if op.dst_path is not None else None,
        "rule_name": op.rule_name,
        "strategy": op.strategy.value if op.strategy is not None else None,
        "size": op.size,
        "mtime_ns": op.mtime_ns,
        "inode": op.inode,
        "device": op.device,
        "sha256": op.sha256,
        "created_dirs": [str(p) for p in op.created_dirs],
        "created_at": op.created_at,
        "finished_at": op.finished_at,
        "undone_at": op.undone_at,
        "error": op.error,
    }


class HistoryService:
    """SQLite-backed implementation of the History Protocol.

    Enforces journal-first durability (S5), per-thread database connections,
    comprehensive filtering queries, event logging, and safe pruning.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        """Initialize HistoryService with database path and optional clock.

        Args:
            db_path: Path to the SQLite history database file.
            clock: Callable returning ISO-8601 UTC timestamp string. Defaults to current time.
        """
        self.db_path = Path(db_path)
        self._clock: Callable[[], str] = clock if clock is not None else _default_clock
        self._local = threading.local()

        if str(self.db_path) != ":memory:":
            init_conn = connect(self.db_path)
            init_conn.close()

    def now_iso(self) -> str:
        """Return the current ISO-8601 UTC timestamp using the injected clock."""
        return self._clock()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a per-thread SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.db_path)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close the connection associated with the current thread."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def begin_batch(
        self,
        command: str,
        folder: Path | None = None,
        note: str | None = None,
    ) -> int:
        """Insert a batch row and return the newly generated batch_id."""
        conn = self._get_conn()
        with _write_tx(conn):
            folder_str = pathcodec.encode(folder) if folder is not None else None
            cur = conn.execute(
                """
                INSERT INTO batches (created_at, command, folder, note)
                VALUES (?, ?, ?, ?);
                """,
                (self.now_iso(), command, folder_str, note),
            )
            batch_id = cur.lastrowid
            assert batch_id is not None
            return batch_id

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        """Retrieve batch metadata by batch_id."""
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM batches WHERE id = ?;", (batch_id,))
        row = cur.fetchone()
        if row is None:
            return None
        folder_raw = row["folder"]
        return {
            "id": int(row["id"]),
            "created_at": str(row["created_at"]),
            "command": str(row["command"]),
            "folder": pathcodec.decode(folder_raw) if folder_raw is not None else None,
            "note": row["note"],
        }

    def begin_op(
        self,
        batch_id: int,
        src: Path,
        rule_name: str | None,
        file: FileInfo,
    ) -> int:
        """Insert status=pending BEFORE any filesystem change.

        Must be committed and durable on return (journal-first per S5).
        Returns op_id.
        """
        conn = self._get_conn()
        with _write_tx(conn):
            cur = conn.execute(
                """
                INSERT INTO operations (
                    batch_id, status, src_path, dst_path, rule_name, strategy,
                    size, mtime_ns, inode, device, sha256, created_dirs,
                    created_at, finished_at, undone_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    batch_id,
                    OpStatus.PENDING.value,
                    pathcodec.encode(src),
                    None,
                    rule_name,
                    None,
                    file.size,
                    file.mtime_ns,
                    file.inode,
                    file.device,
                    None,
                    json.dumps([]),
                    self.now_iso(),
                    None,
                    None,
                    None,
                ),
            )
            op_id = cur.lastrowid
            assert op_id is not None
            return op_id

    def finish_op(self, op_id: int, result: MoveResult) -> None:
        """Mark operation done or failed with final destination, strategy, and created dirs."""
        conn = self._get_conn()
        status = OpStatus.DONE.value if result.ok else OpStatus.FAILED.value
        dst_str = pathcodec.encode(result.dst) if result.dst is not None else None
        strat_str = result.strategy.value if result.strategy is not None else None
        dirs_json = json.dumps([pathcodec.encode(p) for p in result.created_dirs])
        error_str = None
        if not result.ok:
            error_str = result.message or (result.code.value if result.code else "failed")
        now = self.now_iso()

        with _write_tx(conn):
            cur = conn.execute(
                """
                UPDATE operations
                SET status = ?, dst_path = ?, strategy = ?, created_dirs = ?,
                    finished_at = ?, error = ?
                WHERE id = ?;
                """,
                (status, dst_str, strat_str, dirs_json, now, error_str, op_id),
            )
            if cur.rowcount == 0:
                msg = f"Operation id {op_id} not found"
                raise KeyError(msg)

    def record_event(
        self,
        kind: str,
        message: str,
        *,
        level: str = "info",
        batch_id: int | None = None,
        op_id: int | None = None,
        path: Path | None = None,
    ) -> None:
        """Insert an event log record."""
        conn = self._get_conn()
        path_str = pathcodec.encode(path) if path is not None else None
        with _write_tx(conn):
            conn.execute(
                """
                INSERT INTO events (ts, level, kind, batch_id, op_id, path, message)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (self.now_iso(), level, kind, batch_id, op_id, path_str, message),
            )

    def _row_to_op(self, row: sqlite3.Row) -> OperationRecord:
        """Convert a database row into a frozen OperationRecord dataclass."""
        dst_raw = row["dst_path"]
        strategy_raw = row["strategy"]
        created_dirs_raw = row["created_dirs"]

        created_dirs: tuple[Path, ...] = ()
        if created_dirs_raw:
            try:
                parsed = json.loads(created_dirs_raw)
                if isinstance(parsed, list):
                    created_dirs = tuple(pathcodec.decode(p) for p in parsed)
            except Exception:
                created_dirs = ()

        return OperationRecord(
            id=int(row["id"]),
            batch_id=int(row["batch_id"]),
            status=OpStatus(row["status"]),
            src_path=pathcodec.decode(row["src_path"]),
            dst_path=pathcodec.decode(dst_raw) if dst_raw is not None else None,
            rule_name=row["rule_name"],
            strategy=MoveStrategy(strategy_raw) if strategy_raw is not None else None,
            size=int(row["size"]) if row["size"] is not None else None,
            mtime_ns=int(row["mtime_ns"]) if row["mtime_ns"] is not None else None,
            inode=int(row["inode"]) if row["inode"] is not None else None,
            device=int(row["device"]) if row["device"] is not None else None,
            sha256=row["sha256"],
            created_dirs=created_dirs,
            created_at=str(row["created_at"]),
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
            undone_at=str(row["undone_at"]) if row["undone_at"] is not None else None,
            error=str(row["error"]) if row["error"] is not None else None,
        )

    def get_op(self, op_id: int) -> OperationRecord | None:
        """Retrieve an OperationRecord by op_id, or None if not found."""
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM operations WHERE id = ?;", (op_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_op(row)

    def _resolve_since(self, since_val: str) -> str:
        """Resolve since_iso argument which may be a relative duration or ISO string."""
        try:
            secs = parse_duration_seconds(since_val)
            base_dt = _parse_iso(self.now_iso())
            cutoff_dt = base_dt - timedelta(seconds=secs)
            return cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return since_val

    def list_ops(
        self,
        *,
        batch_id: int | None = None,
        status: OpStatus | None = None,
        limit: int = 50,
        since_iso: str | None = None,
        rule_name: str | None = None,
        path_contains: str | None = None,
        order: str = "desc",
    ) -> Sequence[OperationRecord]:
        """List operations matching the specified filter criteria."""
        clauses: list[str] = []
        params: list[Any] = []

        if batch_id is not None:
            clauses.append("batch_id = ?")
            params.append(batch_id)

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)

        if rule_name is not None:
            clauses.append("rule_name = ?")
            params.append(rule_name)

        if since_iso is not None:
            effective_since = self._resolve_since(since_iso)
            clauses.append("created_at >= ?")
            params.append(effective_since)

        if path_contains is not None:
            clauses.append("(src_path LIKE ? OR dst_path LIKE ?)")
            pattern = f"%{path_contains}%"
            params.extend([pattern, pattern])

        order_sql = "ASC" if order.lower() == "asc" else "DESC"
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM operations {where_sql} ORDER BY id {order_sql} LIMIT ?;"  # noqa: S608
        params.append(limit)

        conn = self._get_conn()
        cur = conn.execute(sql, tuple(params))
        return [self._row_to_op(row) for row in cur.fetchall()]

    def last_batch_id(self, *, with_status: OpStatus = OpStatus.DONE) -> int | None:
        """Find the highest batch_id that has at least one operation with with_status."""
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT MAX(batch_id) FROM operations WHERE status = ?;",
            (with_status.value,),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def mark_undone(self, op_id: int) -> None:
        """Set status=undone and undone_at=now for the specified operation."""
        conn = self._get_conn()
        with _write_tx(conn):
            cur = conn.execute(
                "UPDATE operations SET status = ?, undone_at = ? WHERE id = ?;",
                (OpStatus.UNDONE.value, self.now_iso(), op_id),
            )
            if cur.rowcount == 0:
                msg = f"Operation id {op_id} not found"
                raise KeyError(msg)

    def pending_ops(self) -> Sequence[OperationRecord]:
        """Return all operations currently in pending status, ordered by id ASC."""
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT * FROM operations WHERE status = ? ORDER BY id ASC;",
            (OpStatus.PENDING.value,),
        )
        return [self._row_to_op(row) for row in cur.fetchall()]

    def prune(self, older_than_days: int) -> int:
        """Delete non-pending operations and events older than cutoff.

        Empty batches are also removed. Never deletes operations with status='pending'.
        Never deletes any user files on the filesystem.
        Returns the number of operations deleted.
        """
        if older_than_days < 0:
            msg = f"older_than_days must be non-negative: {older_than_days}"
            raise ValueError(msg)

        now_dt = _parse_iso(self.now_iso())
        cutoff_dt = now_dt - timedelta(days=older_than_days)
        cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        conn = self._get_conn()
        with _write_tx(conn):
            cur_ops = conn.execute(
                """
                DELETE FROM operations
                WHERE created_at < ? AND status != ?;
                """,
                (cutoff_iso, OpStatus.PENDING.value),
            )
            deleted_ops = cur_ops.rowcount

            conn.execute(
                "DELETE FROM events WHERE ts < ?;",
                (cutoff_iso,),
            )

            conn.execute(
                """
                DELETE FROM batches
                WHERE id NOT IN (SELECT DISTINCT batch_id FROM operations);
                """,
            )

            return deleted_ops

    def event_list(
        self,
        *,
        batch_id: int | None = None,
        op_id: int | None = None,
        since_iso: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve events matching optional batch_id, op_id, or since_iso filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if batch_id is not None:
            clauses.append("batch_id = ?")
            params.append(batch_id)

        if op_id is not None:
            clauses.append("op_id = ?")
            params.append(op_id)

        if since_iso is not None:
            effective_since = self._resolve_since(since_iso)
            clauses.append("ts >= ?")
            params.append(effective_since)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM events {where_sql} ORDER BY id ASC LIMIT ?;"  # noqa: S608
        params.append(limit)

        conn = self._get_conn()
        cur = conn.execute(sql, tuple(params))
        results: list[dict[str, Any]] = []
        for row in cur.fetchall():
            path_raw = row["path"]
            results.append(
                {
                    "id": int(row["id"]),
                    "ts": str(row["ts"]),
                    "level": str(row["level"]),
                    "kind": str(row["kind"]),
                    "batch_id": int(row["batch_id"]) if row["batch_id"] is not None else None,
                    "op_id": int(row["op_id"]) if row["op_id"] is not None else None,
                    "path": pathcodec.decode(path_raw) if path_raw is not None else None,
                    "message": str(row["message"]),
                }
            )
        return results
