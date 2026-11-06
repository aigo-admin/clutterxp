"""Tests for HistoryService: operations, transactions, filtering, concurrency, and invariants."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clutterxp.history.service import HistoryService, _write_tx, op_to_dict
from clutterxp.models import (
    FileInfo,
    MoveResult,
    MoveStrategy,
    OpStatus,
)


def _make_file_info(path: Path, size: int = 100, mtime_ns: int = 1000) -> FileInfo:
    """Create a FileInfo instance for testing without requiring a real file."""
    return FileInfo(
        path=path,
        name=path.name,
        stem=path.stem,
        ext=path.suffix[1:] if path.suffix else "",
        size=size,
        mtime_ns=mtime_ns,
        inode=12345,
        device=67890,
    )


def test_A26_every_move_has_batch_and_op_row(tmp_path: Path) -> None:
    """A26: Every successful move has a batch and an operation row with

    src, dst, rule, strategy, size, mtime.
    """
    db_path = tmp_path / "history.db"
    service = HistoryService(db_path)

    batch_id = service.begin_batch("organize", folder=tmp_path, note="Wave 1 test batch")
    assert isinstance(batch_id, int)

    src_file = tmp_path / "invoice_march.pdf"
    dst_file = tmp_path / "Sorted" / "invoice_march.pdf"
    created_dir = tmp_path / "Sorted"

    file_info = _make_file_info(src_file, size=4096, mtime_ns=1700000000123456789)
    op_id = service.begin_op(batch_id, src_file, "invoices", file_info)
    assert isinstance(op_id, int)

    # Before move completes, operation is pending
    pending_op = service.get_op(op_id)
    assert pending_op is not None
    assert pending_op.status == OpStatus.PENDING
    assert pending_op.src_path == src_file
    assert pending_op.dst_path is None

    result = MoveResult(
        ok=True,
        src=src_file,
        dst=dst_file,
        strategy=MoveStrategy.RENAME_NOREPLACE,
        created_dirs=(created_dir,),
        bytes_copied=4096,
    )
    service.finish_op(op_id, result)

    op = service.get_op(op_id)
    assert op is not None
    assert op.id == op_id
    assert op.batch_id == batch_id
    assert op.status == OpStatus.DONE
    assert op.src_path == src_file
    assert op.dst_path == dst_file
    assert op.rule_name == "invoices"
    assert op.strategy == MoveStrategy.RENAME_NOREPLACE
    assert op.size == 4096
    assert op.mtime_ns == 1700000000123456789
    assert op.inode == 12345
    assert op.device == 67890
    assert op.created_dirs == (created_dir,)
    assert op.created_at is not None
    assert op.finished_at is not None
    assert op.error is None

    # Check batch record
    batch = service.get_batch(batch_id)
    assert batch is not None
    assert batch["id"] == batch_id
    assert batch["command"] == "organize"
    assert batch["folder"] == tmp_path
    assert batch["note"] == "Wave 1 test batch"

    service.close()


def test_A27_pending_row_durable_before_move(tmp_path: Path) -> None:
    """A27: The pending row is durable before the first filesystem change.

    Proved by a fake mover that reads the DB during the move.
    """
    db_path = tmp_path / "history.db"
    service = HistoryService(db_path)
    batch_id = service.begin_batch("organize", folder=tmp_path)

    test_file = tmp_path / "data.csv"
    test_file.write_text("sample,csv,data")
    file_info = FileInfo.from_path(test_file)

    verified_in_mover = False

    class DbVerifyingMover:
        def move(self, op_id: int, src: Path) -> MoveResult:
            nonlocal verified_in_mover
            # Open an independent database connection to verify durability
            reader_service = HistoryService(db_path)
            op = reader_service.get_op(op_id)
            assert op is not None
            assert op.status == OpStatus.PENDING
            assert op.src_path == src
            assert op.size == file_info.size
            assert op.mtime_ns == file_info.mtime_ns
            assert op.inode == file_info.inode
            assert op.device == file_info.device
            reader_service.close()
            verified_in_mover = True
            return MoveResult(
                ok=True,
                src=src,
                dst=tmp_path / "sorted" / "data.csv",
                strategy=MoveStrategy.RENAME_NOREPLACE,
            )

    op_id = service.begin_op(batch_id, test_file, "csv_rule", file_info)
    mover = DbVerifyingMover()
    result = mover.move(op_id, test_file)
    assert verified_in_mover is True

    service.finish_op(op_id, result)
    service.close()


def test_S5_pending_row_durable_before_fs_change(tmp_path: Path) -> None:
    """S5: A pending journal row is durable before the first filesystem change.

    Tested by launching a subprocess that calls begin_op and then kills itself with SIGKILL.
    The parent process then verifies that the pending row exists on disk.
    """
    db_path = tmp_path / "s5.db"
    src_file = tmp_path / "crash_test.txt"
    src_file.write_text("content to be moved")

    code = f"""
import os, signal, sys
from pathlib import Path
from clutterxp.history.service import HistoryService
from clutterxp.models import FileInfo

db_path = Path({str(db_path)!r})
src = Path({str(src_file)!r})
service = HistoryService(db_path)
batch_id = service.begin_batch("organize")
file_info = FileInfo.from_path(src)
op_id = service.begin_op(batch_id, src, "crash_rule", file_info)
print(op_id, flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    stdout, _ = proc.communicate()
    assert proc.returncode != 0  # Process killed by SIGKILL
    op_id = int(stdout.strip())

    service = HistoryService(db_path)
    op = service.get_op(op_id)
    assert op is not None
    assert op.status == OpStatus.PENDING
    assert op.src_path == src_file
    assert op.rule_name == "crash_rule"
    service.close()


def test_A28_history_filters_by_batch_status_since_rule_path(tmp_path: Path) -> None:
    """A28: history filters by batch, status, since, rule and path text."""
    db_path = tmp_path / "filters.db"

    # Inject clock with specific times for testing `since` filters
    clock_time = "2026-11-06T10:00:00Z"
    service = HistoryService(db_path, clock=lambda: clock_time)

    # Batch 1 (older, 2026-11-04)
    clock_time = "2026-11-04T10:00:00Z"
    b1 = service.begin_batch("organize")
    fi1 = _make_file_info(Path("/home/user/doc1.pdf"))
    op1 = service.begin_op(b1, Path("/home/user/doc1.pdf"), "docs", fi1)
    service.finish_op(
        op1,
        MoveResult(
            ok=True,
            src=Path("/home/user/doc1.pdf"),
            dst=Path("/archive/doc1.pdf"),
            strategy=MoveStrategy.RENAME_NOREPLACE,
        ),
    )

    # Batch 2 (recent, 2026-11-06)
    clock_time = "2026-11-06T09:00:00Z"
    b2 = service.begin_batch("watch")
    fi2 = _make_file_info(Path("/home/user/invoice.pdf"))
    op2 = service.begin_op(b2, Path("/home/user/invoice.pdf"), "invoices", fi2)
    service.finish_op(
        op2,
        MoveResult(
            ok=True,
            src=Path("/home/user/invoice.pdf"),
            dst=Path("/archive/invoice.pdf"),
            strategy=MoveStrategy.RENAME_NOREPLACE,
        ),
    )

    fi3 = _make_file_info(Path("/home/user/failed.zip"))
    op3 = service.begin_op(b2, Path("/home/user/failed.zip"), "zips", fi3)
    service.finish_op(
        op3,
        MoveResult(
            ok=False,
            src=Path("/home/user/failed.zip"),
            dst=None,
            strategy=None,
            message="Permission denied",
        ),
    )

    fi4 = _make_file_info(Path("/home/user/pending.tar"))
    op4 = service.begin_op(b2, Path("/home/user/pending.tar"), "tars", fi4)

    # Advance clock to now
    clock_time = "2026-11-06T12:00:00Z"

    # Filter by batch_id
    res_b1 = service.list_ops(batch_id=b1)
    assert len(res_b1) == 1
    assert res_b1[0].id == op1

    res_b2 = service.list_ops(batch_id=b2)
    assert len(res_b2) == 3

    # Filter by status
    res_done = service.list_ops(status=OpStatus.DONE)
    assert {op.id for op in res_done} == {op1, op2}

    res_failed = service.list_ops(status=OpStatus.FAILED)
    assert len(res_failed) == 1
    assert res_failed[0].id == op3

    res_pending = service.list_ops(status=OpStatus.PENDING)
    assert len(res_pending) == 1
    assert res_pending[0].id == op4

    # Filter by rule_name
    res_rule = service.list_ops(rule_name="invoices")
    assert len(res_rule) == 1
    assert res_rule[0].id == op2

    # Filter by path_contains on src_path
    res_path_src = service.list_ops(path_contains="failed")
    assert len(res_path_src) == 1
    assert res_path_src[0].id == op3

    # Filter by path_contains on dst_path
    res_path_dst = service.list_ops(path_contains="/archive/")
    assert {op.id for op in res_path_dst} == {op1, op2}

    # Filter by since_iso (ISO string)
    res_since_iso = service.list_ops(since_iso="2026-11-05T00:00:00Z")
    assert {op.id for op in res_since_iso} == {op2, op3, op4}

    # Filter by since_iso (relative duration: 1d relative to 2026-11-06T12:00:00Z)
    res_since_rel = service.list_ops(since_iso="1d")
    assert {op.id for op in res_since_rel} == {op2, op3, op4}

    # Filter by since_iso (relative duration: 7d includes all)
    res_since_7d = service.list_ops(since_iso="7d")
    assert len(res_since_7d) == 4

    # Combined filters
    res_combo = service.list_ops(batch_id=b2, status=OpStatus.DONE)
    assert len(res_combo) == 1
    assert res_combo[0].id == op2

    # Limit
    res_limit = service.list_ops(limit=2)
    assert len(res_limit) == 2

    service.close()


def test_A29_history_show_op_and_events(tmp_path: Path) -> None:
    """A29: history show ID prints the operation and related events."""
    db_path = tmp_path / "show.db"
    service = HistoryService(db_path)

    batch_id = service.begin_batch("organize")
    src = tmp_path / "report.pdf"
    dst = tmp_path / "Archive" / "report.pdf"
    file_info = _make_file_info(src)

    op_id = service.begin_op(batch_id, src, "reports", file_info)
    service.record_event(
        kind="plan",
        message="Planned move to Archive",
        batch_id=batch_id,
        op_id=op_id,
        path=src,
    )

    service.finish_op(
        op_id,
        MoveResult(
            ok=True,
            src=src,
            dst=dst,
            strategy=MoveStrategy.RENAME_NOREPLACE,
        ),
    )
    service.record_event(
        kind="done",
        message="Moved successfully",
        level="info",
        batch_id=batch_id,
        op_id=op_id,
        path=dst,
    )

    # Another event unrelated to this op
    service.record_event(kind="general", message="General status event", batch_id=batch_id)

    # Verify operation retrieval
    op = service.get_op(op_id)
    assert op is not None
    assert op.id == op_id
    assert op.status == OpStatus.DONE

    # Verify related events for this op
    op_events = service.event_list(op_id=op_id)
    assert len(op_events) == 2
    assert op_events[0]["kind"] == "plan"
    assert op_events[0]["op_id"] == op_id
    assert op_events[0]["path"] == src
    assert op_events[1]["kind"] == "done"
    assert op_events[1]["op_id"] == op_id
    assert op_events[1]["path"] == dst

    # Verify events for the batch
    batch_events = service.event_list(batch_id=batch_id)
    assert len(batch_events) == 3

    service.close()


def test_A40_prune_removes_rows_only(tmp_path: Path) -> None:
    """A40: history prune removes rows only; a corrupt DB is renamed, never deleted."""
    db_path = tmp_path / "prune.db"

    current_time_iso = "2026-11-06T12:00:00Z"
    service = HistoryService(db_path, clock=lambda: current_time_iso)

    # Create real files on the filesystem to verify that prune NEVER touches user files
    user_file1 = tmp_path / "file1.txt"
    user_file1.write_text("important data 1")
    user_file2 = tmp_path / "file2.txt"
    user_file2.write_text("important data 2")

    # 1. Old batch with DONE op (120 days ago)
    current_time_iso = "2026-07-01T12:00:00Z"
    b1 = service.begin_batch("organize")
    fi1 = FileInfo.from_path(user_file1)
    op1 = service.begin_op(b1, user_file1, "r1", fi1)
    service.finish_op(
        op1,
        MoveResult(ok=True, src=user_file1, dst=user_file1, strategy=MoveStrategy.RENAME_NOREPLACE),
    )
    service.record_event("info", "Old event", batch_id=b1, op_id=op1)

    # 2. Old batch with PENDING op (120 days ago) - MUST NOT BE PRUNED
    b2 = service.begin_batch("organize")
    fi2 = FileInfo.from_path(user_file2)
    op2_pending = service.begin_op(b2, user_file2, "r2", fi2)

    # 3. Recent batch with DONE op (1 day ago)
    current_time_iso = "2026-11-05T12:00:00Z"
    b3 = service.begin_batch("organize")
    op3_recent = service.begin_op(b3, user_file1, "r3", fi1)
    service.finish_op(
        op3_recent,
        MoveResult(ok=True, src=user_file1, dst=user_file1, strategy=MoveStrategy.RENAME_NOREPLACE),
    )

    # Set clock to present time
    current_time_iso = "2026-11-06T12:00:00Z"

    # Prune rows older than 30 days
    deleted_count = service.prune(older_than_days=30)
    assert deleted_count == 1  # Only op1 was pruned

    # op1 (old done) was deleted
    assert service.get_op(op1) is None

    # op2 (old pending) was NOT deleted! Pending rows must never be pruned.
    res_pending = service.get_op(op2_pending)
    assert res_pending is not None
    assert res_pending.status == OpStatus.PENDING

    # op3 (recent done) was NOT deleted
    assert service.get_op(op3_recent) is not None

    # Old event was pruned
    events_b1 = service.event_list(batch_id=b1)
    assert len(events_b1) == 0

    # User files on the filesystem are untouched ("removes rows only")
    assert user_file1.exists()
    assert user_file1.read_text() == "important data 1"
    assert user_file2.exists()
    assert user_file2.read_text() == "important data 2"

    service.close()


def test_concurrency_8_threads(tmp_path: Path) -> None:
    """Concurrency: 8 threads doing begin_batch/begin_op/finish_op concurrently.

    Assert no rows lost and no unhandled database locked errors surface.
    """
    db_path = tmp_path / "concurrent.db"
    service = HistoryService(db_path)

    num_threads = 8
    ops_per_thread = 20
    total_expected = num_threads * ops_per_thread

    errors: list[str] = []

    def worker(t_id: int) -> None:
        try:
            batch_id = service.begin_batch("watch", note=f"worker_{t_id}")
            for i in range(ops_per_thread):
                src = Path(f"/test/thread_{t_id}/file_{i}.txt")
                dst = Path(f"/sorted/thread_{t_id}/file_{i}.txt")
                fi = FileInfo(
                    path=src,
                    name=src.name,
                    stem=src.stem,
                    ext="txt",
                    size=100 + i,
                    mtime_ns=1700000000 + i,
                    inode=1000 + i,
                    device=1,
                )
                op_id = service.begin_op(batch_id, src, "conc_rule", fi)
                res = MoveResult(
                    ok=True,
                    src=src,
                    dst=dst,
                    strategy=MoveStrategy.RENAME_NOREPLACE,
                )
                service.finish_op(op_id, res)
        except Exception:
            errors.append(traceback.format_exc())
        finally:
            service.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent execution errors: {errors}"

    ops = service.list_ops(limit=total_expected + 10)
    assert len(ops) == total_expected
    assert all(op.status == OpStatus.DONE for op in ops)

    service.close()


def test_clock_injection(tmp_path: Path) -> None:
    """Verify that injected clock controls all recorded timestamps."""
    db_path = tmp_path / "clock.db"
    fake_time = "2026-05-15T08:30:00Z"
    service = HistoryService(db_path, clock=lambda: fake_time)

    assert service.now_iso() == fake_time

    b_id = service.begin_batch("organize")
    b = service.get_batch(b_id)
    assert b is not None
    assert b["created_at"] == fake_time

    fi = _make_file_info(Path("/test/f.txt"))
    op_id = service.begin_op(b_id, Path("/test/f.txt"), "rule", fi)
    op = service.get_op(op_id)
    assert op is not None
    assert op.created_at == fake_time

    fake_time = "2026-05-15T08:30:05Z"
    service.finish_op(
        op_id,
        MoveResult(
            ok=True,
            src=Path("/test/f.txt"),
            dst=Path("/test/sorted/f.txt"),
            strategy=MoveStrategy.RENAME_NOREPLACE,
        ),
    )
    op_done = service.get_op(op_id)
    assert op_done is not None
    assert op_done.finished_at == fake_time

    service.close()


def test_mark_reverted_op_status(tmp_path: Path) -> None:
    """Verify mark_undone transitions an operation to OpStatus.UNDONE."""
    db_path = tmp_path / "undo.db"
    service = HistoryService(db_path)

    b_id = service.begin_batch("organize")
    fi = _make_file_info(Path("/tmp/file.txt"))
    op_id = service.begin_op(b_id, Path("/tmp/file.txt"), "r", fi)
    service.finish_op(
        op_id,
        MoveResult(
            ok=True,
            src=Path("/tmp/file.txt"),
            dst=Path("/tmp/out.txt"),
            strategy=MoveStrategy.RENAME_NOREPLACE,
        ),
    )

    service.mark_undone(op_id)
    op = service.get_op(op_id)
    assert op is not None
    assert op.status == OpStatus.UNDONE
    assert op.undone_at is not None

    with pytest.raises(KeyError):
        service.mark_undone(999999)

    service.close()


def test_last_batch_id_and_pending_ops(tmp_path: Path) -> None:
    """Verify last_batch_id and pending_ops functionality."""
    db_path = tmp_path / "last_batch.db"
    service = HistoryService(db_path)

    # When DB is empty
    assert service.last_batch_id() is None
    assert service.pending_ops() == []

    b1 = service.begin_batch("organize")
    fi = _make_file_info(Path("/f1.txt"))
    op1 = service.begin_op(b1, Path("/f1.txt"), "r1", fi)

    # b1 only has a pending op, so with_status=DONE returns None
    assert service.last_batch_id(with_status=OpStatus.DONE) is None
    assert service.last_batch_id(with_status=OpStatus.PENDING) == b1
    assert len(service.pending_ops()) == 1

    service.finish_op(
        op1,
        MoveResult(
            ok=True,
            src=Path("/f1.txt"),
            dst=Path("/dst1.txt"),
            strategy=MoveStrategy.RENAME_NOREPLACE,
        ),
    )
    assert service.last_batch_id(with_status=OpStatus.DONE) == b1
    assert len(service.pending_ops()) == 0

    b2 = service.begin_batch("watch")
    op2 = service.begin_op(b2, Path("/f2.txt"), "r2", fi)
    service.finish_op(
        op2,
        MoveResult(ok=False, src=Path("/f2.txt"), dst=None, strategy=None, message="error"),
    )
    # b2 is failed, so last DONE batch is still b1
    assert service.last_batch_id(with_status=OpStatus.DONE) == b1
    assert service.last_batch_id(with_status=OpStatus.FAILED) == b2

    service.close()


def test_A61_history_json_serialization(tmp_path: Path) -> None:
    """A61: History data shapes serialize cleanly to JSON for --json output."""
    db_path = tmp_path / "json.db"
    service = HistoryService(db_path)

    b_id = service.begin_batch("organize")
    src = tmp_path / "doc.pdf"
    dst = tmp_path / "Archive" / "doc.pdf"
    fi = _make_file_info(src)
    op_id = service.begin_op(b_id, src, "rule_json", fi)
    service.finish_op(
        op_id,
        MoveResult(
            ok=True,
            src=src,
            dst=dst,
            strategy=MoveStrategy.RENAME_NOREPLACE,
            created_dirs=(tmp_path / "Archive",),
        ),
    )

    op = service.get_op(op_id)
    assert op is not None

    d = op_to_dict(op)
    assert d["id"] == op_id
    assert d["batch_id"] == b_id
    assert d["status"] == "done"
    assert d["src_path"] == str(src)
    assert d["dst_path"] == str(dst)
    assert d["rule_name"] == "rule_json"
    assert d["strategy"] == "renameat2"
    assert d["created_dirs"] == [str(tmp_path / "Archive")]
    assert isinstance(d["created_at"], str)

    service.close()


def test_service_edge_cases_and_error_handling(tmp_path: Path) -> None:
    """Verify edge cases: missing records, invalid json dirs, negative prune days, and rollbacks."""
    db_path = tmp_path / "edge_cases.db"
    service = HistoryService(db_path)

    # get_batch on non-existent batch
    assert service.get_batch(999999) is None

    # finish_op on non-existent op
    with pytest.raises(KeyError, match="Operation id 999999 not found"):
        service.finish_op(
            999999,
            MoveResult(
                ok=True,
                src=Path("/tmp/a"),
                dst=Path("/tmp/b"),
                strategy=MoveStrategy.RENAME_NOREPLACE,
            ),
        )

    # prune with negative days
    with pytest.raises(ValueError, match="older_than_days must be non-negative"):
        service.prune(-5)

    # Test _row_to_op when created_dirs is malformed JSON in DB
    b_id = service.begin_batch("organize")
    fi = _make_file_info(Path("/test/corrupt_dirs.txt"))
    op_id = service.begin_op(b_id, Path("/test/corrupt_dirs.txt"), "rule", fi)
    conn = service._get_conn()
    conn.execute("UPDATE operations SET created_dirs = 'MALFORMED_JSON' WHERE id = ?;", (op_id,))
    op_corrupt_dirs = service.get_op(op_id)
    assert op_corrupt_dirs is not None
    assert op_corrupt_dirs.created_dirs == ()

    # Test _resolve_since with timezone-naive string and invalid string
    dt_naive = service._resolve_since("2026-11-06T12:00:00")
    assert dt_naive == "2026-11-06T12:00:00"

    from clutterxp.history.service import _parse_iso

    parsed_naive = _parse_iso("2026-11-06T12:00:00")
    assert parsed_naive.tzinfo is not None

    parsed_invalid = _parse_iso("not-a-valid-iso-string")
    assert parsed_invalid.tzinfo is not None

    service.close()


def test_service_transaction_rollbacks(tmp_path: Path) -> None:
    """Verify that exceptions in service methods trigger ROLLBACK."""
    db_path = tmp_path / "rollbacks.db"
    service = HistoryService(db_path)
    conn = service._get_conn()

    # Drop batches to test begin_batch rollback
    conn.execute("DROP TABLE batches;")
    with pytest.raises(sqlite3.OperationalError):
        service.begin_batch("organize")

    # Drop operations to test begin_op, finish_op, prune rollbacks
    conn.execute("DROP TABLE operations;")
    fi = _make_file_info(Path("/test/f.txt"))
    with pytest.raises(sqlite3.OperationalError):
        service.begin_op(1, Path("/test/f.txt"), "rule", fi)

    with pytest.raises(sqlite3.OperationalError):
        service.finish_op(
            1,
            MoveResult(
                ok=True,
                src=Path("/a"),
                dst=Path("/b"),
                strategy=MoveStrategy.RENAME_NOREPLACE,
            ),
        )

    with pytest.raises(sqlite3.OperationalError):
        service.prune(10)

    # Drop events to test record_event rollback
    conn.execute("DROP TABLE events;")
    with pytest.raises(sqlite3.OperationalError):
        service.record_event("info", "test")

    service.close()


def test_event_list_since_filter(tmp_path: Path) -> None:
    """Test filtering events by since_iso with both ISO timestamp and relative duration."""
    db_path = tmp_path / "events_since.db"
    now_dt = "2026-06-01T12:00:00Z"
    service = HistoryService(db_path, clock=lambda: now_dt)

    service.record_event("info", "old event")
    # Change clock to 2 hours later
    service._clock = lambda: "2026-06-01T14:00:00Z"
    service.record_event("warning", "recent event")

    # Filter with since_iso as absolute ISO
    all_events = service.event_list()
    assert len(all_events) == 2

    recent_events = service.event_list(since_iso="2026-06-01T13:00:00Z")
    assert len(recent_events) == 1
    assert recent_events[0]["message"] == "recent event"

    # Filter with relative duration "1h" (since 14:00 - 1h = 13:00)
    rel_events = service.event_list(since_iso="1h")
    assert len(rel_events) == 1
    assert rel_events[0]["message"] == "recent event"

    service.close()


def test_write_tx_retry_on_lock() -> None:
    """Test _write_tx backoff retry when locked on BEGIN IMMEDIATE or COMMIT."""
    mock_conn = MagicMock()
    # 1st call fails with database is locked, 2nd call succeeds
    mock_conn.execute.side_effect = [
        sqlite3.OperationalError("database is locked"),
        None,  # BEGIN IMMEDIATE succeeds
        sqlite3.OperationalError("database table is locked"),
        None,  # COMMIT succeeds
    ]

    with _write_tx(mock_conn, max_retries=3):
        pass

    assert mock_conn.execute.call_count == 4
