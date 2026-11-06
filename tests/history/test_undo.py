"""Tests for UndoService and acceptance criteria A30-A37, A39, and invariants S3, S4."""

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

from clutterxp.history.service import HistoryService
from clutterxp.history.undo import UndoService
from clutterxp.models import (
    CreateDestinations,
    ErrorCode,
    FileInfo,
    OpStatus,
)
from clutterxp.mover.safe_move import SafeMover
from tests.conftest import EnvPaths, SnapshotFn


def _perform_move(
    history: HistoryService,
    mover: SafeMover,
    src: Path,
    dest_dir: Path,
    *,
    batch_id: int | None = None,
    rule_name: str = "test_rule",
    new_name: str | None = None,
) -> tuple[int, int, Path]:
    """Helper to execute a real journal-first move and return (batch_id, op_id, dst_path)."""
    if batch_id is None:
        batch_id = history.begin_batch("organize", folder=src.parent)
    file_info = FileInfo.from_path(src)
    op_id = history.begin_op(batch_id, src, rule_name, file_info)
    res = mover.move_file(
        src,
        dest_dir,
        new_name=new_name,
        create=CreateDestinations.ALL,
    )
    assert res.ok, f"Move failed: {res.message}"
    assert res.dst is not None
    history.finish_op(op_id, res)
    return batch_id, op_id, res.dst


def test_A30_undo_last_batch_default(env: EnvPaths, tmp_path: Path) -> None:
    """A30: Undo with no selectors undos all operations in the last DONE batch in LIFO order."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = tmp_path / "organized"

    # Batch 1: two files
    f1 = src_dir / "file1.txt"
    f2 = src_dir / "file2.txt"
    f1.write_text("content 1", encoding="utf-8")
    f2.write_text("content 2", encoding="utf-8")
    b1_id, _op1, dst1 = _perform_move(history, mover, f1, dest_dir / "b1")
    _perform_move(history, mover, f2, dest_dir / "b1", batch_id=b1_id)

    # Batch 2: two files
    f3 = src_dir / "file3.txt"
    f4 = src_dir / "file4.txt"
    f3.write_text("content 3", encoding="utf-8")
    f4.write_text("content 4", encoding="utf-8")
    b2_id, op3, dst3 = _perform_move(history, mover, f3, dest_dir / "b2")
    _, op4, dst4 = _perform_move(history, mover, f4, dest_dir / "b2", batch_id=b2_id)

    assert not f3.exists()
    assert not f4.exists()
    assert dst3.exists()
    assert dst4.exists()

    # Default undo should undo batch 2 in reverse order (op4 then op3)
    outcomes = undo_service.undo()
    assert len(outcomes) == 2
    assert outcomes[0].op_id == op4
    assert outcomes[0].ok is True
    assert outcomes[0].restored_to == f4
    assert outcomes[1].op_id == op3
    assert outcomes[1].ok is True
    assert outcomes[1].restored_to == f3

    # Files in batch 2 restored to original locations
    assert f3.exists() and f3.read_text(encoding="utf-8") == "content 3"
    assert f4.exists() and f4.read_text(encoding="utf-8") == "content 4"
    assert not dst3.exists()
    assert not dst4.exists()

    # Batch 1 files still moved
    assert dst1.exists()

    # Next default undo undos batch 1
    outcomes_b1 = undo_service.undo()
    assert len(outcomes_b1) == 2
    assert f1.exists() and f1.read_text(encoding="utf-8") == "content 1"
    assert not dst1.exists()

    # Next undo has nothing left to undo
    assert undo_service.undo() == []


def test_A31_undo_by_op_batch_and_file(env: EnvPaths, tmp_path: Path) -> None:
    """A31: Undo by explicit selectors: op_id, batch_id, and file_path."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = tmp_path / "organized"

    f1 = src_dir / "f1.txt"
    f2 = src_dir / "f2.txt"
    f3 = src_dir / "f3.txt"
    f1.write_text("c1", encoding="utf-8")
    f2.write_text("c2", encoding="utf-8")
    f3.write_text("c3", encoding="utf-8")

    batch_id, op1, dst1 = _perform_move(history, mover, f1, dest_dir)
    _, op2, dst2 = _perform_move(history, mover, f2, dest_dir, batch_id=batch_id)
    _, op3, dst3 = _perform_move(history, mover, f3, dest_dir, batch_id=batch_id)

    # 1. Selector op_id: undo only op2
    outcomes_op = undo_service.undo(op_id=op2)
    assert len(outcomes_op) == 1
    assert outcomes_op[0].ok is True
    assert outcomes_op[0].op_id == op2
    assert f2.exists() and not dst2.exists()
    assert not f1.exists() and dst1.exists()
    assert not f3.exists() and dst3.exists()

    # 2. Selector file_path: undo op1 by dst1
    outcomes_file = undo_service.undo(file_path=dst1)
    assert len(outcomes_file) == 1
    assert outcomes_file[0].ok is True
    assert outcomes_file[0].op_id == op1
    assert f1.exists() and not dst1.exists()
    assert not f3.exists() and dst3.exists()

    # 3. Selector batch_id: undo remaining ops in batch (only op3 remains DONE)
    outcomes_batch = undo_service.undo(batch_id=batch_id)
    assert len(outcomes_batch) == 1
    assert outcomes_batch[0].op_id == op3
    assert outcomes_batch[0].ok is True
    assert f3.exists() and not dst3.exists()

    # Nonexistent op_id
    outcomes_bad_op = undo_service.undo(op_id=999999)
    assert len(outcomes_bad_op) == 1
    assert outcomes_bad_op[0].ok is False
    assert outcomes_bad_op[0].code == ErrorCode.UNDO_NOT_DONE

    # Nonexistent file_path
    assert undo_service.undo(file_path=tmp_path / "nonexistent.txt") == []


def test_A32_undo_refuses_modified_file_allow_modified_waives(
    env: EnvPaths, tmp_path: Path
) -> None:
    """A32: Check 3 refuses modified files; allow_modified waives Check 3."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = tmp_path / "docs"

    # Case 1: size modified
    f1 = src_dir / "file1.txt"
    f1.write_text("original content", encoding="utf-8")
    _b1, op1, dst1 = _perform_move(history, mover, f1, dest_dir)

    # Append to destination
    with dst1.open("a", encoding="utf-8") as f:
        f.write(" - extra appended")

    # Undo refused due to size modification
    out1 = undo_service.undo(op_id=op1)
    assert len(out1) == 1
    assert out1[0].ok is False
    assert out1[0].code == ErrorCode.UNDO_DEST_CHANGED
    assert not f1.exists()

    # allow_modified=True waives the check and restores the file
    out1_waived = undo_service.undo(op_id=op1, allow_modified=True)
    assert len(out1_waived) == 1
    assert out1_waived[0].ok is True
    assert f1.exists()
    assert "extra appended" in f1.read_text(encoding="utf-8")

    # Case 2: mtime modified
    f2 = src_dir / "file2.txt"
    f2.write_text("content 2", encoding="utf-8")
    _b2, op2, dst2 = _perform_move(history, mover, f2, dest_dir)

    # Alter mtime only (keep same size)
    st = dst2.stat()
    new_mtime_ns = st.st_mtime_ns + 5_000_000_000  # +5 seconds
    os.utime(dst2, ns=(st.st_atime_ns, new_mtime_ns))

    out2 = undo_service.undo(op_id=op2)
    assert len(out2) == 1
    assert out2[0].ok is False
    assert out2[0].code == ErrorCode.UNDO_DEST_CHANGED

    out2_waived = undo_service.undo(op_id=op2, allow_modified=True)
    assert len(out2_waived) == 1
    assert out2_waived[0].ok is True
    assert f2.exists()


def test_A33_undo_refuses_occupied_origin_rename_restores(env: EnvPaths, tmp_path: Path) -> None:
    """A33: Occupied origin refuses with skip, restores as <stem> (restored)<ext> with rename."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = tmp_path / "docs"

    f = src_dir / "report.txt"
    f.write_text("original report", encoding="utf-8")
    _, op, dst = _perform_move(history, mover, f, dest_dir)

    # Place a new file at original source path
    f.write_text("new file occupying origin", encoding="utf-8")

    # on_conflict="skip" (default): refuses with UNDO_ORIGIN_OCCUPIED
    out_skip = undo_service.undo(op_id=op, on_conflict="skip")
    assert len(out_skip) == 1
    assert out_skip[0].ok is False
    assert out_skip[0].code == ErrorCode.UNDO_ORIGIN_OCCUPIED
    assert f.read_text(encoding="utf-8") == "new file occupying origin"
    assert dst.exists()

    # on_conflict="rename": restores as report (restored).txt
    out_rename = undo_service.undo(op_id=op, on_conflict="rename")
    assert len(out_rename) == 1
    assert out_rename[0].ok is True
    restored_path = src_dir / "report (restored).txt"
    assert out_rename[0].restored_to == restored_path
    assert restored_path.exists()
    assert restored_path.read_text(encoding="utf-8") == "original report"
    assert f.read_text(encoding="utf-8") == "new file occupying origin"
    assert not dst.exists()

    # Compound extension: archive.tar.gz
    tar_src = src_dir / "bundle.tar.gz"
    tar_src.write_bytes(b"tar gz content")
    _, tar_op, _tar_dst = _perform_move(history, mover, tar_src, dest_dir)

    tar_src.write_bytes(b"new tar bundle occupying origin")
    out_tar = undo_service.undo(op_id=tar_op, on_conflict="rename")
    assert len(out_tar) == 1
    assert out_tar[0].ok is True
    expected_tar_restored = src_dir / "bundle (restored).tar.gz"
    assert out_tar[0].restored_to == expected_tar_restored
    assert expected_tar_restored.exists()
    assert expected_tar_restored.read_bytes() == b"tar gz content"


def test_A34_undo_never_overwrites(env: EnvPaths, tmp_path: Path) -> None:
    """A34 and S3: Undo never clobbers or overwrites existing files at restored destination."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = tmp_path / "docs"

    f = src_dir / "contract.pdf"
    f.write_text("contract v1", encoding="utf-8")
    _, op, dst = _perform_move(history, mover, f, dest_dir)

    # Origin is occupied
    f.write_text("contract v2 occupant", encoding="utf-8")
    # Even the rename candidate <stem> (restored)<ext> is ALREADY occupied
    restored_cand = src_dir / "contract (restored).pdf"
    restored_cand.write_text("existing restored occupant", encoding="utf-8")

    # Attempt undo with on_conflict="rename"
    out = undo_service.undo(op_id=op, on_conflict="rename")
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.COLLISION

    # Invariant S3: NO file overwritten, all contents intact
    assert f.read_text(encoding="utf-8") == "contract v2 occupant"
    assert restored_cand.read_text(encoding="utf-8") == "existing restored occupant"
    assert dst.read_text(encoding="utf-8") == "contract v1"


def test_A35_undo_recreates_missing_parent_removes_empty_created_dirs(
    env: EnvPaths, tmp_path: Path
) -> None:
    """A35 and S4: Missing parent directory is recreated;
    empty created dirs removed; non-empty untouched.
    """
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads" / "sub" / "deep"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = tmp_path / "organized" / "tier1" / "tier2"

    f = src_dir / "item.txt"
    f.write_text("item content", encoding="utf-8")
    _, op, dst = _perform_move(history, mover, f, dest_dir)

    # Completely delete original parent directories
    import shutil

    shutil.rmtree(tmp_path / "downloads")
    assert not src_dir.exists()

    # Also put another user file in tier1 so tier1 is NOT empty
    other_file = tmp_path / "organized" / "tier1" / "other.txt"
    other_file.write_text("do not delete me", encoding="utf-8")

    # Undo
    out = undo_service.undo(op_id=op)
    assert len(out) == 1
    assert out[0].ok is True
    assert f.exists()
    assert f.read_text(encoding="utf-8") == "item content"

    # Destination file is gone
    assert not dst.exists()

    # tier2 was empty so it was removed; tier1 had other_file so it was preserved (S4)
    assert not (tmp_path / "organized" / "tier1" / "tier2").exists()
    assert (tmp_path / "organized" / "tier1").exists()
    assert other_file.exists()


def test_A36_refusal_does_not_block_batch_undo(env: EnvPaths, tmp_path: Path) -> None:
    """A36 and S15: A refusal on one file does NOT stop or abort remaining files in batch."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = tmp_path / "docs"

    f1 = src_dir / "f1.txt"
    f2 = src_dir / "f2.txt"
    f3 = src_dir / "f3.txt"
    f1.write_text("c1", encoding="utf-8")
    f2.write_text("c2", encoding="utf-8")
    f3.write_text("c3", encoding="utf-8")

    batch_id, op1, _dst1 = _perform_move(history, mover, f1, dest_dir)
    _, op2, dst2 = _perform_move(history, mover, f2, dest_dir, batch_id=batch_id)
    _, op3, _dst3 = _perform_move(history, mover, f3, dest_dir, batch_id=batch_id)

    # Invalidate f2 at destination by modifying it
    dst2.write_text("c2 modified", encoding="utf-8")

    # Undo batch: order is LIFO (op3, op2, op1)
    outcomes = undo_service.undo(batch_id=batch_id)
    assert len(outcomes) == 3

    assert outcomes[0].op_id == op3
    assert outcomes[0].ok is True
    assert f3.exists()

    assert outcomes[1].op_id == op2
    assert outcomes[1].ok is False
    assert outcomes[1].code == ErrorCode.UNDO_DEST_CHANGED
    assert not f2.exists() and dst2.exists()

    assert outcomes[2].op_id == op1
    assert outcomes[2].ok is True
    assert f1.exists()


def test_A37_undoing_already_undone_is_refused(env: EnvPaths, tmp_path: Path) -> None:
    """A37: Calling undo on an already undone operation fails with UNDO_NOT_DONE."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    f = src_dir / "file.txt"
    f.write_text("hello", encoding="utf-8")
    _, op, _ = _perform_move(history, mover, f, tmp_path / "docs")

    # First undo succeeds
    out1 = undo_service.undo(op_id=op)
    assert len(out1) == 1
    assert out1[0].ok is True

    # Second undo on same op fails with UNDO_NOT_DONE
    out2 = undo_service.undo(op_id=op)
    assert len(out2) == 1
    assert out2[0].ok is False
    assert out2[0].code == ErrorCode.UNDO_NOT_DONE


def test_A39_non_utf8_names_undo(env: EnvPaths, tmp_path: Path) -> None:
    """A39: Files with non-UTF-8 byte filenames can be undone without corruption."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src_dir = tmp_path / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)

    raw_bytes = b"bad_\xff_byte_name.dat"
    raw_name = os.fsdecode(raw_bytes)
    f = src_dir / raw_name
    f.write_bytes(b"binary data with invalid utf8 filename")

    _, op, dst = _perform_move(history, mover, f, tmp_path / "organized")
    assert dst.exists()
    assert not f.exists()

    out = undo_service.undo(op_id=op)
    assert len(out) == 1
    assert out[0].ok is True
    assert f.exists()
    assert f.read_bytes() == b"binary data with invalid utf8 filename"
    assert os.fsencode(f.name) == raw_bytes


def test_undo_dry_run_makes_no_changes(env: EnvPaths, tmp_path: Path, snapshot: SnapshotFn) -> None:
    """Dry run evaluates checks and predicts outcome without mutating filesystem or history."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    user_root = tmp_path / "user"
    src_dir = user_root / "downloads"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest_dir = user_root / "docs"

    f = src_dir / "sample.txt"
    f.write_text("sample content", encoding="utf-8")
    _batch_id, op, _dst = _perform_move(history, mover, f, dest_dir)

    # Snapshot user tree before dry-run
    before_snap = snapshot(user_root)
    events_before = len(history.event_list())
    op_before = history.get_op(op)
    assert op_before is not None and op_before.status == OpStatus.DONE

    # Execute dry-run
    outcomes = undo_service.undo(dry_run=True)
    assert len(outcomes) == 1
    assert outcomes[0].ok is True
    assert outcomes[0].restored_to == f
    assert outcomes[0].code == ErrorCode.NONE

    # Tree and DB state completely unchanged
    after_snap = snapshot(user_root)
    assert after_snap == before_snap
    assert len(history.event_list()) == events_before
    op_after = history.get_op(op)
    assert op_after is not None and op_after == op_before


@pytest.mark.crossfs
def test_undo_cross_filesystem(env: EnvPaths, tmp_path: Path, second_fs: Path) -> None:
    """Undo restores files across distinct filesystems."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src = tmp_path / "cross_file.bin"
    src.write_bytes(b"cross-fs test content " * 100)
    dest_dir = second_fs / "dest_folder"

    _, op, dst = _perform_move(history, mover, src, dest_dir)
    assert dst.exists()
    assert not src.exists()

    out = undo_service.undo(op_id=op)
    assert len(out) == 1
    assert out[0].ok is True
    assert src.exists()
    assert src.read_bytes() == b"cross-fs test content " * 100
    assert not dst.exists()


def test_undo_destination_missing_refused(env: EnvPaths, tmp_path: Path) -> None:
    """Check 2: missing destination file produces UNDO_DEST_MISSING."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src = tmp_path / "file.txt"
    src.write_text("hello", encoding="utf-8")
    _, op, dst = _perform_move(history, mover, src, tmp_path / "docs")

    # Manually remove destination
    dst.unlink()

    out = undo_service.undo(op_id=op)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.UNDO_DEST_MISSING


def test_undo_destination_directory_unwritable_refused(env: EnvPaths, tmp_path: Path) -> None:
    """Check 5: unwritable parent directory produces DEST_NOT_WRITABLE."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    parent_dir = tmp_path / "protected"
    parent_dir.mkdir(parents=True, exist_ok=True)
    src = parent_dir / "file.txt"
    src.write_text("hello", encoding="utf-8")
    _, op, _ = _perform_move(history, mover, src, tmp_path / "docs")

    # Make parent_dir unwritable
    parent_dir.chmod(0o500)
    try:
        out = undo_service.undo(op_id=op)
        assert len(out) == 1
        assert out[0].ok is False
        assert out[0].code == ErrorCode.DEST_NOT_WRITABLE
    finally:
        parent_dir.chmod(0o700)


def test_undo_on_conflict_invalid_raises(env: EnvPaths) -> None:
    """Invalid on_conflict policy raises ValueError."""
    history = HistoryService(env.state_dir / "history.db")
    undo_service = UndoService(history, SafeMover(), env.state_dir / "fs.lock")
    with pytest.raises(ValueError, match="Invalid on_conflict"):
        undo_service.undo(on_conflict="destroy")


def test_undo_sha256_verification(env: EnvPaths, tmp_path: Path) -> None:
    """Check 3: recorded sha256 mismatch triggers UNDO_DEST_CHANGED unless allow_modified=True."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src = tmp_path / "sha_file.txt"
    src.write_text("hello 1", encoding="utf-8")
    _, op, _dst = _perform_move(history, mover, src, tmp_path / "docs")

    # Record fake sha256 in DB
    conn = history._get_conn()
    conn.execute("UPDATE operations SET sha256 = 'abc123expected' WHERE id = ?;", (op,))
    conn.commit()

    # Mismatch check triggers
    out1 = undo_service.undo(op_id=op)
    assert len(out1) == 1
    assert out1[0].ok is False
    assert out1[0].code == ErrorCode.UNDO_DEST_CHANGED
    assert "hash mismatch" in out1[0].message

    # allow_modified waives it
    out2 = undo_service.undo(op_id=op, allow_modified=True)
    assert len(out2) == 1
    assert out2[0].ok is True
    assert src.exists()


def test_undo_status_not_done_refused(env: EnvPaths, tmp_path: Path) -> None:
    """Check 1: Non-DONE operation statuses refuse with UNDO_NOT_DONE."""
    history = HistoryService(env.state_dir / "history.db")
    undo_service = UndoService(history, SafeMover(), env.state_dir / "fs.lock")

    b_id = history.begin_batch("organize", folder=tmp_path)
    src = tmp_path / "p_file.txt"
    src.write_text("pending file", encoding="utf-8")
    op_id = history.begin_op(b_id, src, "rule", FileInfo.from_path(src))

    # Op is PENDING
    out = undo_service.undo(op_id=op_id)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.UNDO_NOT_DONE


def test_undo_dst_path_none_refused(env: EnvPaths, tmp_path: Path) -> None:
    """Check 2: op with dst_path=None produces UNDO_DEST_MISSING."""
    history = HistoryService(env.state_dir / "history.db")
    undo_service = UndoService(history, SafeMover(), env.state_dir / "fs.lock")

    src = tmp_path / "n_file.txt"
    src.write_text("data", encoding="utf-8")
    _b_id, op_id, _dst = _perform_move(history, SafeMover(), src, tmp_path / "docs")

    conn = history._get_conn()
    conn.execute("UPDATE operations SET dst_path = NULL WHERE id = ?;", (op_id,))
    conn.commit()

    out = undo_service.undo(op_id=op_id)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.UNDO_DEST_MISSING


def test_undo_dst_not_regular_file_refused(env: EnvPaths, tmp_path: Path) -> None:
    """Check 2: destination is a directory produces UNDO_DEST_MISSING."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src = tmp_path / "file_reg.txt"
    src.write_text("data", encoding="utf-8")
    _, op_id, dst = _perform_move(history, mover, src, tmp_path / "docs")

    # Replace destination file with a directory
    dst.unlink()
    dst.mkdir()

    out = undo_service.undo(op_id=op_id)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.UNDO_DEST_MISSING


def test_undo_parent_exists_but_is_a_file_refused(env: EnvPaths, tmp_path: Path) -> None:
    """Check 5: parent exists but is a regular file produces DEST_NOT_WRITABLE."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    parent_dir = tmp_path / "parent_as_file"
    parent_dir.mkdir(parents=True, exist_ok=True)
    src = parent_dir / "file.txt"
    src.write_text("data", encoding="utf-8")
    _, op_id, _dst = _perform_move(history, mover, src, tmp_path / "docs")

    # Remove parent_dir and recreate it as a regular file
    parent_dir.rmdir()
    parent_dir.write_text("I am a file now", encoding="utf-8")

    out = undo_service.undo(op_id=op_id)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.DEST_NOT_WRITABLE


def test_undo_dry_run_reports_collision(env: EnvPaths, tmp_path: Path) -> None:
    """Dry run reports COLLISION when target_path already exists."""
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")

    src = tmp_path / "coll_file.txt"
    src.write_text("orig", encoding="utf-8")
    _, op_id, _dst = _perform_move(history, mover, src, tmp_path / "docs")

    # Place file at src
    src.write_text("new occupant", encoding="utf-8")
    # Place file at src (restored).txt
    cand = tmp_path / "coll_file (restored).txt"
    cand.write_text("cand occupant", encoding="utf-8")

    out = undo_service.undo(op_id=op_id, dry_run=True, on_conflict="rename")
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.COLLISION


def test_undo_mover_failure_reported(env: EnvPaths, tmp_path: Path) -> None:
    """When mover fails, undo returns ok=False with the mover's ErrorCode."""
    from tests.helpers import FakeMover

    history = HistoryService(env.state_dir / "history.db")
    fake_mover = FakeMover(failure_code=ErrorCode.PERMISSION, failure_message="Permission denied")
    undo_service = UndoService(history, fake_mover, env.state_dir / "fs.lock")

    src = tmp_path / "f_perm.txt"
    src.write_text("data", encoding="utf-8")
    _, op_id, _dst = _perform_move(history, SafeMover(), src, tmp_path / "docs")

    out = undo_service.undo(op_id=op_id)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.PERMISSION


def test_undo_path_helper_edge_cases() -> None:
    """Directly test _path_to_str and _matches_file_path helper methods."""
    from clutterxp.history.undo import _path_to_str

    assert _path_to_str(None) == ""
    assert UndoService._matches_file_path(None, Path("/some/path")) is False


def test_undo_lock_timeout_handling(
    env: EnvPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When fs_lock times out, undo catches ClutterError and returns LOCK_TIMEOUT outcome."""
    from clutterxp import locking
    from clutterxp.models import ClutterError

    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")
    src = tmp_path / "lock_file.txt"
    src.write_text("lock test", encoding="utf-8")
    _, op, _dst = _perform_move(history, mover, src, tmp_path / "docs")

    from contextlib import contextmanager

    @contextmanager
    def mock_fs_lock(*args: object, **kwargs: object) -> object:
        raise ClutterError("Simulated lock timeout", code=ErrorCode.LOCK_TIMEOUT)
        yield

    monkeypatch.setattr(locking, "fs_lock", mock_fs_lock)
    out = undo_service.undo(op_id=op)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.LOCK_TIMEOUT


def test_undo_sha256_read_error(
    env: EnvPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When sha256 read fails with OSError, it is treated as hash mismatch."""
    import clutterxp.history.undo as undo_mod

    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")
    src = tmp_path / "sha_err.txt"
    src.write_text("data", encoding="utf-8")
    _, op, _dst = _perform_move(history, mover, src, tmp_path / "docs")

    conn = history._get_conn()
    conn.execute("UPDATE operations SET sha256 = 'abc' WHERE id = ?;", (op,))
    conn.commit()

    def mock_sha(p: Path) -> str:
        raise OSError("Read error")

    monkeypatch.setattr(undo_mod, "_sha256_file", mock_sha)
    out = undo_service.undo(op_id=op)
    assert len(out) == 1
    assert out[0].ok is False
    assert out[0].code == ErrorCode.UNDO_DEST_CHANGED


def test_undo_missing_parent_ancestor_unwritable(env: EnvPaths, tmp_path: Path) -> None:
    """When missing parent directory's nearest ancestor is unwritable,
    fails with DEST_NOT_WRITABLE.
    """
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    undo_service = UndoService(history, mover, env.state_dir / "fs.lock")
    root = tmp_path / "unwritable_ancestor"
    root.mkdir()
    sub = root / "missing_parent"
    sub.mkdir()
    src = sub / "file.txt"
    src.write_text("content", encoding="utf-8")
    _, op, _dst = _perform_move(history, mover, src, tmp_path / "docs")

    sub.rmdir()
    root.chmod(0o500)
    try:
        out = undo_service.undo(op_id=op)
        assert len(out) == 1
        assert out[0].ok is False
        assert out[0].code == ErrorCode.DEST_NOT_WRITABLE
    finally:
        root.chmod(0o700)
