"""Tests for crash recovery service, acceptance criterion A38, and invariants S4, S5."""

# ruff: noqa: E402
from __future__ import annotations

import os
import sys
from pathlib import Path

_worktree_src = Path(__file__).resolve().parents[2] / "src"
if str(_worktree_src) not in sys.path:
    sys.path.insert(0, str(_worktree_src))

import pytest

import clutterxp

_local_clutterxp = _worktree_src / "clutterxp"
if str(_local_clutterxp) not in clutterxp.__path__:
    clutterxp.__path__.insert(0, str(_local_clutterxp))

import clutterxp.history

_local_history = _local_clutterxp / "history"
if str(_local_history) not in clutterxp.history.__path__:
    clutterxp.history.__path__.insert(0, str(_local_history))

from clutterxp.history.recovery import (
    RecoveryAction,
    reconcile,
    reconcile_on_startup,
)
from clutterxp.history.service import HistoryService
from clutterxp.models import (
    FileInfo,
    OpStatus,
)
from clutterxp.mover.safe_move import SafeMover
from tests.conftest import EnvPaths


def test_A38_crash_recovery_all_table_cases_never_deletes_user_files(
    env: EnvPaths, tmp_path: Path
) -> None:
    """A38 and S4: Crash recovery handles all table cases and NEVER deletes user files."""
    history = HistoryService(env.state_dir / "history.db")
    SafeMover()

    user_root = tmp_path / "user"
    src_dir = user_root / "downloads"
    dst_dir = user_root / "docs"
    src_dir.mkdir(parents=True, exist_ok=True)
    dst_dir.mkdir(parents=True, exist_ok=True)

    batch_id = history.begin_batch("organize", folder=src_dir)

    # -------------------------------------------------------------------------
    # Case 1: src exists, dst missing (crashed before move) -> marked failed
    # -------------------------------------------------------------------------
    f1_src = src_dir / "case1.txt"
    f1_src.write_text("case1 content", encoding="utf-8")
    f1_dst = dst_dir / "case1.txt"
    op1_id = history.begin_op(batch_id, f1_src, "rule1", FileInfo.from_path(f1_src))
    # Update dst_path in db for testing (normally begin_op has dst_path=None or planner sets it)
    conn = history._get_conn()
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(f1_dst), op1_id))
    conn.commit()

    # -------------------------------------------------------------------------
    # Case 2: src missing, dst exists with matching size -> marked done
    # -------------------------------------------------------------------------
    f2_src = src_dir / "case2.txt"
    f2_src.write_text("case2 content", encoding="utf-8")
    f2_info = FileInfo.from_path(f2_src)
    op2_id = history.begin_op(batch_id, f2_src, "rule2", f2_info)
    f2_dst = dst_dir / "case2.txt"
    # Destination created with matching content, source removed
    f2_dst.write_text("case2 content", encoding="utf-8")
    f2_src.unlink()
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(f2_dst), op2_id))
    conn.commit()

    # -------------------------------------------------------------------------
    # Case 3: src exists, dst exists (crashed between publish and unlink)
    # Both copies exist -> marked failed with conflict_warning, loud message, NO delete
    # -------------------------------------------------------------------------
    f3_src = src_dir / "case3.txt"
    f3_src.write_text("case3 content", encoding="utf-8")
    f3_info = FileInfo.from_path(f3_src)
    op3_id = history.begin_op(batch_id, f3_src, "rule3", f3_info)
    f3_dst = dst_dir / "case3.txt"
    f3_dst.write_text("case3 content", encoding="utf-8")
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(f3_dst), op3_id))
    conn.commit()

    # -------------------------------------------------------------------------
    # Case 4: src missing, dst missing (external entity moved/removed both)
    # Marked failed ("source and destination missing")
    # -------------------------------------------------------------------------
    f4_src = src_dir / "case4.txt"
    f4_src.write_text("case4 content", encoding="utf-8")
    f4_info = FileInfo.from_path(f4_src)
    op4_id = history.begin_op(batch_id, f4_src, "rule4", f4_info)
    f4_dst = dst_dir / "case4.txt"
    f4_src.unlink()  # both missing
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(f4_dst), op4_id))
    conn.commit()

    # -------------------------------------------------------------------------
    # Case 5a: dst_path None, but exactly 1 matching file in planned destination dir
    # -------------------------------------------------------------------------
    f5a_src = src_dir / "case5a.txt"
    f5a_src.write_text("case5a content", encoding="utf-8")
    f5a_info = FileInfo.from_path(f5a_src)
    op5a_id = history.begin_op(batch_id, f5a_src, "rule5a", f5a_info)
    # dst_path remains NULL in operations table!
    f5a_dst = dst_dir / "case5a.txt"
    f5a_dst.write_text("case5a content", encoding="utf-8")
    # Source unlinked, destination in dst_dir matches name, size, mtime
    st = f5a_dst.stat()
    conn.execute(
        "UPDATE operations SET mtime_ns = ? WHERE id = ?;",
        (st.st_mtime_ns, op5a_id),
    )
    conn.commit()
    f5a_src.unlink()

    # -------------------------------------------------------------------------
    # Case 5b: dst_path None, 0 matching files in planned destination dir
    # Marked failed
    # -------------------------------------------------------------------------
    f5b_src = src_dir / "case5b.txt"
    f5b_src.write_text("case5b content", encoding="utf-8")
    f5b_info = FileInfo.from_path(f5b_src)
    op5b_id = history.begin_op(batch_id, f5b_src, "rule5b", f5b_info)
    # dst_path is NULL, no file in dst_dir

    # Run reconcile!
    actions = reconcile(history, planned_dirs=[dst_dir])

    # Map actions by op_id
    action_map: dict[int, RecoveryAction] = {a.op_id: a for a in actions}

    # Verify Case 1
    assert op1_id in action_map
    assert action_map[op1_id].action_taken == "marked_failed"
    assert "rolled back" in action_map[op1_id].message
    op1_rec = history.get_op(op1_id)
    assert op1_rec is not None and op1_rec.status == OpStatus.FAILED

    # Verify Case 2
    assert op2_id in action_map
    assert action_map[op2_id].action_taken == "marked_done"
    assert action_map[op2_id].dst_path == f2_dst
    op2_rec = history.get_op(op2_id)
    assert op2_rec is not None and op2_rec.status == OpStatus.DONE

    # Verify Case 3: loud warning, both copies exist, neither copy deleted!
    assert op3_id in action_map
    assert action_map[op3_id].action_taken == "conflict_warning"
    assert "both copies exist" in action_map[op3_id].message
    assert str(f3_src) in action_map[op3_id].message
    assert str(f3_dst) in action_map[op3_id].message
    op3_rec = history.get_op(op3_id)
    assert op3_rec is not None and op3_rec.status == OpStatus.FAILED
    # INVARIANT S4: user files are NOT deleted!
    assert f3_src.exists() and f3_src.read_text(encoding="utf-8") == "case3 content"
    assert f3_dst.exists() and f3_dst.read_text(encoding="utf-8") == "case3 content"

    # Verify Case 4
    assert op4_id in action_map
    assert action_map[op4_id].action_taken == "marked_failed"
    assert "missing" in action_map[op4_id].message
    op4_rec = history.get_op(op4_id)
    assert op4_rec is not None and op4_rec.status == OpStatus.FAILED

    # Verify Case 5a: exactly 1 match found in planned destination -> marked done
    assert op5a_id in action_map
    assert action_map[op5a_id].action_taken == "marked_done"
    assert action_map[op5a_id].dst_path == f5a_dst
    op5a_rec = history.get_op(op5a_id)
    assert op5a_rec is not None and op5a_rec.status == OpStatus.DONE

    # Verify Case 5b: 0 matches -> marked failed (rolled back: not moved)
    assert op5b_id in action_map
    assert action_map[op5b_id].action_taken == "marked_failed"
    has_rollback_msg = (
        "rolled back" in action_map[op5b_id].message or "not moved" in action_map[op5b_id].message
    )
    assert has_rollback_msg
    op5b_rec = history.get_op(op5b_id)
    assert op5b_rec is not None and op5b_rec.status == OpStatus.FAILED

    # INVARIANT S4: All surviving user files are still intact
    assert f1_src.read_text(encoding="utf-8") == "case1 content"
    assert f2_dst.read_text(encoding="utf-8") == "case2 content"
    assert f3_src.read_text(encoding="utf-8") == "case3 content"
    assert f3_dst.read_text(encoding="utf-8") == "case3 content"
    assert f5a_dst.read_text(encoding="utf-8") == "case5a content"


def test_reconcile_on_startup_filters_by_process_start(env: EnvPaths, tmp_path: Path) -> None:
    """reconcile_on_startup only processes operations created strictly before startup_time_iso."""
    history = HistoryService(env.state_dir / "history.db")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    batch_id = history.begin_batch("organize", folder=src_dir)

    # Op 1: created in the past (before startup)
    f1 = src_dir / "old_file.txt"
    f1.write_text("old", encoding="utf-8")
    op1_id = history.begin_op(batch_id, f1, "rule1", FileInfo.from_path(f1))

    # Op 2: created in the future (after startup)
    f2 = src_dir / "new_file.txt"
    f2.write_text("new", encoding="utf-8")
    op2_id = history.begin_op(batch_id, f2, "rule2", FileInfo.from_path(f2))

    # Artificially set timestamps in DB
    conn = history._get_conn()
    conn.execute(
        "UPDATE operations SET created_at = '2026-11-06T10:00:00Z' WHERE id = ?;",
        (op1_id,),
    )
    conn.execute(
        "UPDATE operations SET created_at = '2026-11-06T10:10:00Z' WHERE id = ?;",
        (op2_id,),
    )
    conn.commit()

    startup_time = "2026-11-06T10:05:00Z"

    actions = reconcile_on_startup(history, startup_time)

    # Only op1 was created before startup_time
    assert len(actions) == 1
    assert actions[0].op_id == op1_id
    assert actions[0].action_taken == "marked_failed"  # f1 exists, dst missing

    # op2 remains PENDING
    op2_rec = history.get_op(op2_id)
    assert op2_rec is not None and op2_rec.status == OpStatus.PENDING


@pytest.mark.crash
def test_crash_injection_sweep(env: EnvPaths, tmp_path: Path) -> None:
    """Crash injection sweep: simulate crashes at each step and verify recovery."""
    history = HistoryService(env.state_dir / "history.db")
    SafeMover()

    src_dir = tmp_path / "downloads"
    dest_dir = tmp_path / "organized"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.mkdir(parents=True, exist_ok=True)

    batch_id = history.begin_batch("organize", folder=src_dir)

    # -------------------------------------------------------------------------
    # Crash Point 1: Crash immediately after begin_op (before any mover call)
    # S5: Pending row is durable in DB before mover starts.
    # -------------------------------------------------------------------------
    f1 = src_dir / "crash1.txt"
    f1.write_text("crash1 data", encoding="utf-8")
    op1_id = history.begin_op(batch_id, f1, "rule1", FileInfo.from_path(f1))

    # Assert pending row is immediately durable in DB
    fresh_history = HistoryService(env.state_dir / "history.db")
    op1_db = fresh_history.get_op(op1_id)
    assert op1_db is not None and op1_db.status == OpStatus.PENDING

    # "Simulate crash and reboot": reconcile runs on fresh_history
    actions1 = reconcile(fresh_history)
    assert any(a.op_id == op1_id and a.action_taken == "marked_failed" for a in actions1)
    assert f1.exists() and f1.read_text(encoding="utf-8") == "crash1 data"

    # -------------------------------------------------------------------------
    # Crash Point 2: Crash after destination published, before source unlinked
    # -------------------------------------------------------------------------
    f2 = src_dir / "crash2.txt"
    f2.write_text("crash2 data", encoding="utf-8")
    op2_id = fresh_history.begin_op(batch_id, f2, "rule2", FileInfo.from_path(f2))
    dst2 = dest_dir / "crash2.txt"
    dst2.write_text("crash2 data", encoding="utf-8")
    conn = fresh_history._get_conn()
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(dst2), op2_id))
    conn.commit()

    actions2 = reconcile(fresh_history)
    assert any(
        a.op_id == op2_id
        and a.action_taken == "conflict_warning"
        and "both copies exist" in a.message
        for a in actions2
    )
    # INVARIANT S4: user files are NOT deleted
    assert f2.exists() and dst2.exists()

    # -------------------------------------------------------------------------
    # Crash Point 3: Crash after source unlinked, before finish_op called
    # -------------------------------------------------------------------------
    f3 = src_dir / "crash3.txt"
    f3.write_text("crash3 data", encoding="utf-8")
    f3_info = FileInfo.from_path(f3)
    op3_id = fresh_history.begin_op(batch_id, f3, "rule3", f3_info)
    dst3 = dest_dir / "crash3.txt"
    dst3.write_text("crash3 data", encoding="utf-8")
    f3.unlink()  # source unlinked
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(dst3), op3_id))
    conn.commit()

    actions3 = reconcile(fresh_history)
    assert any(a.op_id == op3_id and a.action_taken == "marked_done" for a in actions3)
    op3_final = fresh_history.get_op(op3_id)
    assert op3_final is not None and op3_final.status == OpStatus.DONE
    assert dst3.exists() and dst3.read_text(encoding="utf-8") == "crash3 data"


def test_reconcile_handles_non_utf8_paths(env: EnvPaths, tmp_path: Path) -> None:
    """Reconcile handles non-UTF-8 surrogate-escaped filenames without errors."""
    history = HistoryService(env.state_dir / "history.db")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    batch_id = history.begin_batch("organize", folder=src_dir)

    raw_bytes = b"crash_\xfe_file.dat"
    raw_name = os.fsdecode(raw_bytes)
    f = src_dir / raw_name
    f.write_bytes(b"non-utf8 crash data")

    op_id = history.begin_op(batch_id, f, "rule_non_utf8", FileInfo.from_path(f))

    # File was not moved (crashed before move)
    actions = reconcile(history)
    assert len(actions) == 1
    assert actions[0].op_id == op_id
    assert actions[0].action_taken == "marked_failed"
    assert f.exists()
    assert f.read_bytes() == b"non-utf8 crash data"


def test_reconcile_destination_size_mismatch_fails(env: EnvPaths, tmp_path: Path) -> None:
    """Case 2: destination exists but size != op.size -> marked failed."""
    history = HistoryService(env.state_dir / "history.db")

    src = tmp_path / "mismatch.txt"
    src.write_text("hello world", encoding="utf-8")
    dst = tmp_path / "dest.txt"
    # Destination created with DIFFERENT size
    dst.write_text("shorter", encoding="utf-8")

    batch_id = history.begin_batch("organize", folder=tmp_path)
    op_id = history.begin_op(batch_id, src, "rule", FileInfo.from_path(src))
    src.unlink()  # source vanished
    conn = history._get_conn()
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(dst), op_id))
    conn.commit()

    actions = reconcile(history)
    assert len(actions) == 1
    assert actions[0].op_id == op_id
    assert actions[0].action_taken == "marked_failed"
    assert "size mismatch" in actions[0].message
    assert actions[0].dst_path == dst


def test_reconcile_multiple_candidate_matches_fails(env: EnvPaths, tmp_path: Path) -> None:
    """Case 5: dst_path None and multiple candidate files match in planned dirs -> marked failed."""
    history = HistoryService(env.state_dir / "history.db")

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    f = src_dir / "dup.txt"
    f.write_text("duplicate candidate", encoding="utf-8")

    d1 = tmp_path / "planned1"
    d2 = tmp_path / "planned2"
    d1.mkdir(parents=True, exist_ok=True)
    d2.mkdir(parents=True, exist_ok=True)

    cand1 = d1 / "dup.txt"
    cand2 = d2 / "dup.txt"
    cand1.write_text("duplicate candidate", encoding="utf-8")
    cand2.write_text("duplicate candidate", encoding="utf-8")

    batch_id = history.begin_batch("organize", folder=src_dir)
    op_id = history.begin_op(batch_id, f, "rule", FileInfo.from_path(f))
    f.unlink()  # source unlinked

    # Update mtimes to match
    st = cand1.stat()
    os.utime(cand2, ns=(st.st_atime_ns, st.st_mtime_ns))
    conn = history._get_conn()
    conn.execute("UPDATE operations SET mtime_ns = ? WHERE id = ?;", (st.st_mtime_ns, op_id))
    conn.commit()

    # Reconcile with both candidate directories
    actions = reconcile(history, planned_dirs=[d1, d2])
    assert len(actions) == 1
    assert actions[0].op_id == op_id
    assert actions[0].action_taken == "marked_failed"


def test_recovery_helpers_edge_cases() -> None:
    """Test helper functions in recovery module."""
    from clutterxp.history.recovery import _parse_iso, _path_to_str

    assert _path_to_str(None) == ""
    # Parse invalid ISO string falls back to current time
    dt = _parse_iso("not-a-valid-date")
    assert dt.tzinfo is not None
    # Parse naive ISO string gets UTC attached
    dt_naive = _parse_iso("2026-11-06T10:00:00")
    assert dt_naive.tzinfo is not None


def test_recovery_find_matching_dest_branches(env: EnvPaths, tmp_path: Path) -> None:
    """_find_matching_dest skips non-dirs, source parents, and non-regular candidates."""
    from clutterxp.history.recovery import _find_matching_dest

    history = HistoryService(env.state_dir / "history.db")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "cand.txt"
    f.write_text("hello", encoding="utf-8")
    batch_id = history.begin_batch("organize", folder=src_dir)
    op_id = history.begin_op(batch_id, f, "rule", FileInfo.from_path(f))
    op = history.get_op(op_id)
    assert op is not None

    non_dir = tmp_path / "not_a_dir"
    non_dir.write_text("file")

    planned = tmp_path / "planned"
    planned.mkdir()
    sub_dir = planned / "cand.txt"
    sub_dir.mkdir()  # candidate is a directory!

    res = _find_matching_dest(op, [non_dir, src_dir, planned])
    assert res is None


def test_recovery_dst_lstat_oserror(
    env: EnvPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When lstat on destination raises OSError, treated as size mismatch / failed."""
    history = HistoryService(env.state_dir / "history.db")
    src = tmp_path / "err_src.txt"
    src.write_text("hello", encoding="utf-8")
    dst = tmp_path / "err_dst.txt"
    dst.write_text("hello", encoding="utf-8")
    batch_id = history.begin_batch("organize", folder=tmp_path)
    op_id = history.begin_op(batch_id, src, "rule", FileInfo.from_path(src))
    src.unlink()
    conn = history._get_conn()
    conn.execute("UPDATE operations SET dst_path = ? WHERE id = ?;", (str(dst), op_id))
    conn.commit()

    orig_lstat = Path.lstat

    def mock_lstat(self: Path, *args: object, **kwargs: object) -> object:
        if self == dst:
            raise OSError("lstat error")
        return orig_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", mock_lstat)
    actions = reconcile(history)
    assert len(actions) == 1
    assert actions[0].action_taken == "marked_failed"
    assert "size mismatch" in actions[0].message
