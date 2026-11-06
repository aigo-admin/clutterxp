"""Tests for duplicate quarantine module (v1.1 / A88)."""

from __future__ import annotations

from pathlib import Path

import pytest

from clutterxp.duplicates.quarantine import (
    apply_quarantine,
    plan_quarantine,
    select_kept_file,
)
from clutterxp.history.service import HistoryService
from clutterxp.history.undo import UndoService
from clutterxp.models import DuplicateGroup, FileInfo, OpStatus
from clutterxp.mover.safe_move import SafeMover


def test_select_kept_file_strategies(tmp_path: Path) -> None:
    f1 = tmp_path / "a_old.txt"
    f2 = tmp_path / "b_new.txt"
    f3 = tmp_path / "sub" / "c_long_path.txt"
    f3.parent.mkdir()

    f1.write_text("dup")
    f2.write_text("dup")
    f3.write_text("dup")

    info1 = FileInfo.from_path(f1)
    # simulate newer mtime for f2
    info2 = FileInfo(
        path=f2,
        name=f2.name,
        stem=f2.stem,
        ext="txt",
        size=3,
        mtime_ns=info1.mtime_ns + 1000000,
        inode=100,
        device=info1.device,
    )
    info3 = FileInfo.from_path(f3)

    files = [info1, info2, info3]

    # Oldest
    assert select_kept_file(files, "oldest") == info1

    # Newest
    assert select_kept_file(files, "newest") == info2

    # Shortest path
    assert select_kept_file(files, "shortest-path") in (info1, info2)

    # Specific keep path
    assert select_kept_file(files, "oldest", keep_path=f3) == info3

    # Unknown strategy
    with pytest.raises(ValueError, match="Unknown keep strategy"):
        select_kept_file(files, "invalid")


def test_A88_quarantine_plan_apply_and_undo(tmp_path: Path) -> None:
    """A88: duplicates quarantine previews, confirms, moves (never deletes), is undoable."""
    folder = tmp_path / "data"
    quarantine_dir = tmp_path / "quarantine"
    folder.mkdir()
    quarantine_dir.mkdir()

    f1 = folder / "doc1.txt"
    f2 = folder / "doc2.txt"
    f1.write_text("exact duplicate content")
    f2.write_text("exact duplicate content")

    import os

    info1 = FileInfo.from_path(f1)
    os.utime(f2, ns=(info1.mtime_ns + 1_000_000_000, info1.mtime_ns + 1_000_000_000))
    info2 = FileInfo.from_path(f2)

    group = DuplicateGroup(
        kind="exact",
        files=(info1, info2),
        sha256="abc123hash",
        wasted_bytes=info1.size,
    )

    # 1. Preview (plan)
    plan = plan_quarantine([group], quarantine_dir, keep_strategy="oldest")
    assert plan.total_moves == 1
    assert plan.kept_files == (info1,)
    assert len(plan.actions) == 1
    assert plan.actions[0].file.path == f2

    # Files must still be intact after plan
    assert f1.exists()
    assert f2.exists()

    # 2. Apply quarantine
    mover = SafeMover()
    hist_db = tmp_path / "history.db"
    history = HistoryService(hist_db)
    fs_lock = tmp_path / "fs.lock"

    res = apply_quarantine(plan, mover, history, fs_lock_path=fs_lock)
    assert res.moved == 1
    assert res.failed == 0
    assert res.batch_id is not None

    # Kept file still in original location
    assert f1.exists()
    # Duplicate moved to quarantine dir (never deleted)
    assert not f2.exists()
    quarantined_file = quarantine_dir / "doc2.txt"
    assert quarantined_file.exists()
    assert quarantined_file.read_text() == "exact duplicate content"

    # History recorded batch and done op
    ops = history.list_ops(batch_id=res.batch_id)
    assert len(ops) == 1
    assert ops[0].status == OpStatus.DONE

    # 3. Undo quarantine restores the duplicate to original path
    undo_service = UndoService(history, mover, fs_lock)
    undo_outcomes = undo_service.undo(batch_id=res.batch_id)
    assert len(undo_outcomes) == 1
    assert undo_outcomes[0].ok is True
    assert f2.exists()
    assert not quarantined_file.exists()
    assert f2.read_text() == "exact duplicate content"
