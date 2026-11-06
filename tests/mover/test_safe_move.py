"""Comprehensive tests for SafeMover: preconditions, collision, strategies A and B,

fault injection, hypothesis property tests, and safety invariants.
"""

from __future__ import annotations

import errno
import os
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from clutterxp.locking import fs_lock
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    ErrorCode,
    MoveStrategy,
)
from clutterxp.mover import fsops
from clutterxp.mover.safe_move import SafeMover


# --------------------------------------------------------------------------- Preconditions
def test_precondition_src_vanished(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "nonexistent.txt"
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    res = mover.move_file(src, dest_dir)
    assert res.ok is False
    assert res.code == ErrorCode.SRC_VANISHED


def test_precondition_src_not_regular_dir(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "somedir"
    src.mkdir()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    res = mover.move_file(src, dest_dir)
    assert res.ok is False
    assert res.code == ErrorCode.SRC_NOT_REGULAR


def test_precondition_src_not_regular_symlink(tmp_path: Path) -> None:
    mover = SafeMover()
    target = tmp_path / "target.txt"
    target.write_text("target")
    src = tmp_path / "symlink.txt"
    src.symlink_to(target)

    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    res = mover.move_file(src, dest_dir)
    assert res.ok is False
    assert res.code == ErrorCode.SRC_NOT_REGULAR
    # Target and symlink must be untouched
    assert target.exists()
    assert src.is_symlink()


def test_precondition_src_changed_size(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("12345")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    # Pass expected size 10 (actual is 5)
    res = mover.move_file(src, dest_dir, expect_size=10)
    assert res.ok is False
    assert res.code == ErrorCode.SRC_CHANGED
    assert src.exists()


def test_precondition_src_changed_mtime(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    res = mover.move_file(src, dest_dir, expect_mtime_ns=123456789)
    assert res.ok is False
    assert res.code == ErrorCode.SRC_CHANGED
    assert src.exists()


def test_precondition_dest_invalid_name(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    res = mover.move_file(src, dest_dir, new_name="sub/file.txt")
    assert res.ok is False
    assert res.code == ErrorCode.DEST_INVALID

    res2 = mover.move_file(src, dest_dir, new_name="bad\x00name")
    assert res2.ok is False
    assert res2.code == ErrorCode.DEST_INVALID


def test_precondition_dest_invalid_noop(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")

    # Moving into same directory without new_name is no-op
    res = mover.move_file(src, tmp_path)
    assert res.ok is False
    assert res.code == ErrorCode.DEST_INVALID


def test_precondition_dest_missing_never(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")
    dest_dir = tmp_path / "nonexistent"

    res = mover.move_file(src, dest_dir, create=CreateDestinations.NEVER)
    assert res.ok is False
    assert res.code == ErrorCode.DEST_MISSING
    assert not dest_dir.exists()


def test_precondition_dest_create_leaf(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")

    # Leaf parent exists: dest_dir is created
    dest_dir = tmp_path / "leaf_dir"
    res = mover.move_file(src, dest_dir, create=CreateDestinations.LEAF)
    assert res.ok is True
    assert dest_dir.is_dir()
    assert res.created_dirs == (dest_dir,)

    # Leaf parent does NOT exist: fails with DEST_MISSING
    src2 = tmp_path / "file2.txt"
    src2.write_text("hello2")
    deep_dest = tmp_path / "missing_parent" / "leaf"
    res2 = mover.move_file(src2, deep_dest, create=CreateDestinations.LEAF)
    assert res2.ok is False
    assert res2.code == ErrorCode.DEST_MISSING


def test_precondition_dest_create_leaf_static_root(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")

    static_root = tmp_path / "archive"
    dest_dir = static_root / "2026" / "11"

    # static_root parent (tmp_path) exists, so static_root and subdirs are created
    res = mover.move_file(
        src,
        dest_dir,
        create=CreateDestinations.LEAF,
        static_root=static_root,
    )
    assert res.ok is True
    assert (dest_dir / "file.txt").exists()
    assert static_root in res.created_dirs
    assert dest_dir in res.created_dirs
    assert res.created_dirs[-1] == dest_dir


def test_precondition_dest_create_all(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")
    dest_dir = tmp_path / "a" / "b" / "c"

    res = mover.move_file(src, dest_dir, create=CreateDestinations.ALL)
    assert res.ok is True
    assert dest_dir.is_dir()
    assert (dest_dir / "file.txt").exists()
    assert res.created_dirs == (tmp_path / "a", tmp_path / "a" / "b", dest_dir)


def test_precondition_dest_symlink_rejected(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")

    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_dir"
    sym_dir.symlink_to(real_dir)

    res = mover.move_file(src, sym_dir)
    assert res.ok is False
    assert res.code == ErrorCode.DEST_INVALID


def test_precondition_dest_not_writable(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "file.txt"
    src.write_text("hello")
    dest_dir = tmp_path / "readonly_dir"
    dest_dir.mkdir()
    dest_dir.chmod(0o555)  # Read and execute, but not writable

    try:
        res = mover.move_file(src, dest_dir)
        assert res.ok is False
        assert res.code in (ErrorCode.DEST_NOT_WRITABLE, ErrorCode.PERMISSION)
    finally:
        dest_dir.chmod(0o755)


# --------------------------------------------------------------------------- Acceptance Criteria
def test_A22_race_between_plan_and_publish(tmp_path: Path) -> None:
    """A22: The no-clobber guarantee holds under a race (destination appears after planning)."""
    src = tmp_path / "data.txt"
    src.write_text("source version")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    dst_path = dest_dir / "data.txt"

    # Inject a file at dst_path right before rename_noreplace runs
    class RacingFs:
        def __init__(self, real_fs: Any) -> None:
            self.real = real_fs
            self.injected = False

        def __getattr__(self, name: str) -> Any:
            return getattr(self.real, name)

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            if not self.injected and d == dst_path:
                dst_path.write_text("race winner file")
                self.injected = True
            return self.real.rename_noreplace(s, d)

    racing_fs = RacingFs(fsops)
    mover_skip = SafeMover(fs=racing_fs)

    # 1. With SKIP: race produces COLLISION, race winner is NOT clobbered, src intact
    res_skip = mover_skip.move_file(src, dest_dir, collision=CollisionPolicy.SKIP)
    assert res_skip.ok is False
    assert res_skip.code == ErrorCode.COLLISION
    assert dst_path.read_text() == "race winner file"
    assert src.read_text() == "source version"

    # 2. With RENAME: race causes candidate fallback to data (1).txt, winner not clobbered
    racing_fs2 = RacingFs(fsops)
    mover_rename = SafeMover(fs=racing_fs2)
    res_rename = mover_rename.move_file(src, dest_dir, collision=CollisionPolicy.RENAME)
    assert res_rename.ok is True
    assert res_rename.dst == dest_dir / "data (1).txt"
    assert dst_path.read_text() == "race winner file"
    assert (dest_dir / "data (1).txt").read_text() == "source version"
    assert not src.exists()


@pytest.mark.crossfs
def test_A23_cross_filesystem_with_second_fs(tmp_path: Path, second_fs: Path) -> None:
    """A23: Cross-filesystem moves use copy, fsync, verify, publish, then unlink source."""
    src = tmp_path / "cross_fs_file.bin"
    content = b"cross filesystem binary payload \x00\xff" * 1000
    src.write_bytes(content)

    dest_dir = second_fs / "sorted"
    mover = SafeMover(verify_copy="hash")

    progress_calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        progress_calls.append((done, total))

    res = mover.move_file(src, dest_dir, progress=on_progress)
    assert res.ok is True
    assert res.strategy == MoveStrategy.COPY_VERIFY
    assert res.bytes_copied == len(content)
    assert not src.exists()
    assert res.dst is not None
    assert res.dst.exists()
    assert res.dst.read_bytes() == content
    assert len(progress_calls) > 0


@pytest.mark.crossfs
def test_A23_cross_filesystem_simulated_exdev(tmp_path: Path) -> None:
    """A23: Cross-filesystem simulation when EXDEV is raised by same-FS primitives."""
    src = tmp_path / "file.txt"
    src.write_text("simulated cross-device content")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class ExdevFs:
        def __init__(self, real_fs: Any) -> None:
            self.real = real_fs

        def __getattr__(self, name: str) -> Any:
            return getattr(self.real, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return True  # Claims same device initially

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            # Raise EXDEV on cross-device
            if s == src:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return self.real.rename_noreplace(s, d)

    mover = SafeMover(fs=ExdevFs(fsops))
    res = mover.move_file(src, dest_dir)
    assert res.ok is True
    assert res.strategy == MoveStrategy.COPY_VERIFY
    assert not src.exists()
    assert (dest_dir / "file.txt").read_text() == "simulated cross-device content"


def test_A34_undo_never_overwrites_any_file(tmp_path: Path) -> None:
    """A34: Undo uses SafeMover with collision=SKIP; never overwrites occupied origin."""
    origin = tmp_path / "origin" / "photo.jpg"
    origin.parent.mkdir()
    origin.write_text("newly created file at origin")

    moved_dst = tmp_path / "sorted" / "photo.jpg"
    moved_dst.parent.mkdir()
    moved_dst.write_text("file being restored by undo")

    mover = SafeMover()
    # Undo invokes: move_file(dst, original_dir, new_name=original_name, collision=SKIP, create=ALL)
    result = mover.move_file(
        moved_dst,
        origin.parent,
        new_name=origin.name,
        collision=CollisionPolicy.SKIP,
        create=CreateDestinations.ALL,
    )

    assert result.ok is False
    assert result.code == ErrorCode.COLLISION
    # Crucial: the occupied origin is preserved intact
    assert origin.read_text() == "newly created file at origin"
    assert moved_dst.read_text() == "file being restored by undo"


# --------------------------------------------------------------------------- Fault Injection
@pytest.mark.crash
def test_fault_injection_strategy_b_step1_temp_created(tmp_path: Path) -> None:
    """Crash after temp file created: temp file cleaned up, source intact."""
    src = tmp_path / "src.txt"
    src.write_text("precious source data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class FaultFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def fsync_file(self, p: Path) -> None:
            raise RuntimeError("Simulated crash at step 3")

    mover = SafeMover(fs=FaultFs())
    with pytest.raises(RuntimeError, match="Simulated crash"):
        mover.move_file(src, dest_dir)

    # Source is intact
    assert src.exists()
    assert src.read_text() == "precious source data"
    # No partial temp file left behind
    partials = list(dest_dir.glob("*.clutterxp-partial"))
    assert len(partials) == 0


@pytest.mark.crash
def test_fault_injection_strategy_b_step3_fsync(tmp_path: Path) -> None:
    """Crash during fsync: temp cleaned up, source intact."""
    src = tmp_path / "src.txt"
    src.write_text("source data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class FaultFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def fsync_file(self, p: Path) -> None:
            raise OSError(errno.EIO, "I/O error during fsync")

    mover = SafeMover(fs=FaultFs())
    res = mover.move_file(src, dest_dir)
    assert res.ok is False
    assert src.exists()
    assert src.read_text() == "source data"
    assert len(list(dest_dir.glob("*.clutterxp-partial"))) == 0


@pytest.mark.crash
def test_fault_injection_strategy_b_step7_publish(tmp_path: Path) -> None:
    """Crash right after publish: destination has complete data, source still exists."""
    src = tmp_path / "src.txt"
    src.write_text("verified data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class FaultFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def fsync_dir(self, p: Path) -> None:
            if p == dest_dir:
                # Crash after publish, before unlink
                raise RuntimeError("Crash right after publish")

    mover = SafeMover(fs=FaultFs())
    with pytest.raises(RuntimeError, match="Crash right after publish"):
        mover.move_file(src, dest_dir)

    # In crash state after publish: destination exists and complete, source exists
    assert (dest_dir / "src.txt").exists()
    assert (dest_dir / "src.txt").read_text() == "verified data"
    assert src.exists()
    assert src.read_text() == "verified data"


@pytest.mark.crash
def test_fault_injection_strategy_b_step9_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure at step 9 (unlink src fails): keep both files, return PERMISSION."""
    src = tmp_path / "src.txt"
    src.write_text("verified data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class CrossFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

    orig_unlink = Path.unlink

    def fake_unlink(p: Path) -> None:
        if p == src:
            raise OSError(errno.EACCES, "Permission denied")
        orig_unlink(p)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    mover = SafeMover(fs=CrossFs())
    res = mover.move_file(src, dest_dir)

    assert res.ok is False
    assert res.code == ErrorCode.PERMISSION
    assert "copied but could not remove source" in res.message
    # BOTH files must exist! Destination must NOT be deleted!
    assert src.exists()
    assert (dest_dir / "src.txt").exists()
    assert (dest_dir / "src.txt").read_text() == "verified data"


def test_strategy_b_src_changed_mid_copy(tmp_path: Path) -> None:
    """Source modified during Strategy B copy: aborted with SRC_CHANGED."""
    src = tmp_path / "src.txt"
    src.write_text("initial content")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class MutatingFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def fsync_file(self, p: Path) -> None:
            # Modify source while copy is happening
            src.write_text("tampered content while copying")
            fsops.fsync_file(p)

    mover = SafeMover(fs=MutatingFs())
    res = mover.move_file(src, dest_dir)

    assert res.ok is False
    assert res.code == ErrorCode.SRC_CHANGED
    assert not (dest_dir / "src.txt").exists()
    assert len(list(dest_dir.glob("*.clutterxp-partial"))) == 0


def test_strategy_b_hash_mismatch(tmp_path: Path) -> None:
    """Hash verification failure deletes temp and returns VERIFY_FAILED."""
    src = tmp_path / "src.txt"
    src.write_text("hello")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class TamperFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def fsync_file(self, p: Path) -> None:
            # Corrupt the temp file content
            p.write_text("corrupted")
            fsops.fsync_file(p)


def test_strategy_b_zero_bytes_progress(tmp_path: Path) -> None:
    src = tmp_path / "zero.txt"
    src.write_bytes(b"")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    calls: list[tuple[int, int]] = []

    class CrossFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

    mover = SafeMover(fs=CrossFs())
    res = mover.move_file(src, dest_dir, progress=lambda d, t: calls.append((d, t)))
    assert res.ok is True
    assert (0, 0) in calls
    assert (dest_dir / "zero.txt").exists()


def test_strategy_b_size_mismatch(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("hello world")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class TruncatingFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def fsync_file(self, p: Path) -> None:
            # Truncate temp file
            p.write_bytes(b"short")
            fsops.fsync_file(p)

    mover = SafeMover(fs=TruncatingFs())
    res = mover.move_file(src, dest_dir)
    assert res.ok is False
    assert res.code == ErrorCode.VERIFY_FAILED


def test_strategy_b_src_changed_after_hash(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("initial")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class CrossFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

    def tamper_hash(p: Path) -> str:
        # Mutate src on disk but return identical hash so step 4 hash check passes
        if p == src:
            src.write_text("mutated after hash")
        return "fake_matching_hash"

    with patch("clutterxp.mover.safe_move._hash_file", side_effect=tamper_hash):
        mover = SafeMover(verify_copy="hash", fs=CrossFs())
        res = mover.move_file(src, dest_dir)
        assert res.ok is False
        assert res.code == ErrorCode.SRC_CHANGED


def test_strategy_b_skip_and_rename_collisions(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("new content")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "src.txt").write_text("existing")

    class CrossFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

    mover = SafeMover(fs=CrossFs())

    # SKIP policy fails immediately
    res_skip = mover.move_file(src, dest_dir, collision=CollisionPolicy.SKIP)
    assert res_skip.ok is False
    assert res_skip.code == ErrorCode.COLLISION

    # RENAME policy creates src (1).txt
    res_rename = mover.move_file(src, dest_dir, collision=CollisionPolicy.RENAME)
    assert res_rename.ok is True
    assert res_rename.dst == dest_dir / "src (1).txt"
    assert (dest_dir / "src (1).txt").read_text() == "new content"
    assert (dest_dir / "src.txt").read_text() == "existing"


def test_strategy_b_publish_race_retry(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("content")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class RacingPublishFs:
        def __init__(self) -> None:
            self.race_injected = False

        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            if not self.race_injected and d.name == "src.txt":
                d.write_text("race winner")
                self.race_injected = True
                return False
            return fsops.rename_noreplace(s, d)

    mover = SafeMover(fs=RacingPublishFs())
    res = mover.move_file(src, dest_dir, collision=CollisionPolicy.RENAME)
    assert res.ok is True
    assert res.dst == dest_dir / "src (1).txt"
    assert (dest_dir / "src.txt").read_text() == "race winner"
    assert (dest_dir / "src (1).txt").read_text() == "content"


def test_strategy_a_primitive_fallbacks(tmp_path: Path) -> None:
    """Strategy A falls through rename_noreplace -> link_unlink -> checked_rename."""
    src = tmp_path / "src.txt"
    src.write_text("fallback data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class FallbackToLinkFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return True

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            return False  # Unsupported

    mover1 = SafeMover(fs=FallbackToLinkFs())
    res1 = mover1.move_file(src, dest_dir)
    assert res1.ok is True
    assert res1.strategy == MoveStrategy.LINK_UNLINK
    assert (dest_dir / "src.txt").read_text() == "fallback data"

    src2 = tmp_path / "src2.txt"
    src2.write_text("checked rename data")

    class FallbackToCheckedFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return True

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            return False

        def link_unlink(self, s: Path, d: Path) -> bool:
            return False

    mover2 = SafeMover(fs=FallbackToCheckedFs())
    res2 = mover2.move_file(src2, dest_dir)
    assert res2.ok is True
    assert res2.strategy == MoveStrategy.CHECKED_RENAME
    assert (dest_dir / "src2.txt").read_text() == "checked rename data"


def test_strategy_a_link_unlink_failure_handling(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class UnlinkFailFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return True

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            return False

        def link_unlink(self, s: Path, d: Path) -> bool:
            os.link(s, d)
            # Unlink fails
            return False

    mover = SafeMover(fs=UnlinkFailFs())
    res = mover.move_file(src, dest_dir)
    assert res.ok is False
    assert res.code == ErrorCode.PERMISSION
    assert (dest_dir / "src.txt").exists()
    assert src.exists()


def test_create_all_and_leaf_edge_cases(tmp_path: Path) -> None:
    mover = SafeMover()
    src = tmp_path / "src.txt"
    src.write_text("data")

    # Destination exists as regular file, not dir
    conflict_file = tmp_path / "not_a_dir"
    conflict_file.write_text("I am a file")
    res = mover.move_file(src, conflict_file, create=CreateDestinations.ALL)
    assert res.ok is False
    assert res.code == ErrorCode.DEST_MISSING

    # Symlink component in CreateDestinations.ALL
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_link"
    sym_dir.symlink_to(real_dir)
    res_sym = mover.move_file(src, sym_dir / "sub", create=CreateDestinations.ALL)
    assert res_sym.ok is False
    assert res_sym.code == ErrorCode.DEST_INVALID

    # Symlink component in CreateDestinations.LEAF under static_root
    static_root = tmp_path / "root"
    static_root.mkdir()
    sym_comp = static_root / "sym_comp"
    sym_comp.symlink_to(real_dir)
    res_leaf_sym = mover.move_file(
        src,
        sym_comp / "leaf",
        create=CreateDestinations.LEAF,
        static_root=static_root,
    )
    assert res_leaf_sym.ok is False
    assert res_leaf_sym.code == ErrorCode.DEST_INVALID


def test_top_level_oserrors(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    # ENOSPC top-level error mapping
    with patch.object(SafeMover, "_move_strategy_a", side_effect=OSError(errno.ENOSPC, "No space")):
        mover = SafeMover()
        res = mover.move_file(src, dest_dir)
        assert res.ok is False
        assert res.code == ErrorCode.NO_SPACE

    # EACCES top-level error mapping
    with patch.object(SafeMover, "_move_strategy_a", side_effect=OSError(errno.EACCES, "Denied")):
        mover = SafeMover()
        res = mover.move_file(src, dest_dir)
        assert res.ok is False
        assert res.code == ErrorCode.PERMISSION

    # lstat EACCES error mapping
    with patch.object(Path, "lstat", side_effect=OSError(errno.EACCES, "Denied")):
        mover = SafeMover()
        res = mover.move_file(src, dest_dir)
        assert res.ok is False
        assert res.code == ErrorCode.PERMISSION


def test_precondition_no_space(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("a" * 1000)
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class CrossFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

    with patch("shutil.disk_usage", return_value=type("Usage", (), {"free": 10})()):
        mover = SafeMover(fs=CrossFs())
        res = mover.move_file(src, dest_dir)
        assert res.ok is False
        assert res.code == ErrorCode.NO_SPACE


def test_strategy_a_and_b_candidates_exhausted(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    # Exhaust candidates in Strategy A
    with patch("clutterxp.mover.safe_move.candidate_names", return_value=iter(["cand1"])):
        (dest_dir / "src.txt").write_text("occupied0")
        (dest_dir / "cand1").write_text("occupied1")
        mover = SafeMover()
        res = mover.move_file(src, dest_dir, collision=CollisionPolicy.RENAME)
        assert res.ok is False
        assert res.code == ErrorCode.COLLISION
        assert "Too many collisions" in res.message

    # Exhaust candidates in Strategy B
    class CrossFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

    src2 = tmp_path / "src2.txt"
    src2.write_text("data2")
    with patch("clutterxp.mover.safe_move.candidate_names", return_value=iter(["cand1"])):
        (dest_dir / "src2.txt").write_text("occupied0")
        mover_b = SafeMover(fs=CrossFs())
        res_b = mover_b.move_file(src2, dest_dir, collision=CollisionPolicy.RENAME)
        assert res_b.ok is False
        assert res_b.code == ErrorCode.COLLISION


def test_publish_exdev_fallbacks(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class ExdevLinkFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return True

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            return False

        def link_unlink(self, s: Path, d: Path) -> bool:
            if s == src:
                raise OSError(errno.EXDEV, "EXDEV")
            return fsops.link_unlink(s, d)

    mover1 = SafeMover(fs=ExdevLinkFs())
    res1 = mover1.move_file(src, dest_dir)
    assert res1.ok is True
    assert res1.strategy == MoveStrategy.COPY_VERIFY

    src2 = tmp_path / "src2.txt"
    src2.write_text("data2")

    class ExdevCheckedFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return True

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            return False

        def link_unlink(self, s: Path, d: Path) -> bool:
            return False

        def checked_rename(self, s: Path, d: Path) -> bool:
            if s == src2:
                raise OSError(errno.EXDEV, "EXDEV")
            return fsops.checked_rename(s, d)


def test_create_leaf_static_root_parent_missing(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    static_root = tmp_path / "nonexistent_parent" / "root"
    dest_dir = static_root / "leaf"

    mover = SafeMover()
    res = mover.move_file(src, dest_dir, create=CreateDestinations.LEAF, static_root=static_root)
    assert res.ok is False
    assert res.code == ErrorCode.DEST_MISSING
    assert "Static root parent" in res.message


def test_create_leaf_not_relative_parent_exists(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    static_root = tmp_path / "root"
    static_root.mkdir()
    other_parent = tmp_path / "other"
    other_parent.mkdir()
    dest_dir = other_parent / "leaf"

    mover = SafeMover()
    res = mover.move_file(src, dest_dir, create=CreateDestinations.LEAF, static_root=static_root)
    assert res.ok is True
    assert (dest_dir / "src.txt").exists()


def test_create_all_already_exists(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "existing_all"
    dest_dir.mkdir()

    mover = SafeMover()
    res = mover.move_file(src, dest_dir, create=CreateDestinations.ALL)
    assert res.ok is True
    assert (dest_dir / "src.txt").exists()


def test_top_level_enoent_mapping(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    with patch.object(SafeMover, "_move_strategy_a", side_effect=OSError(errno.ENOENT, "Vanished")):
        mover = SafeMover()
        res = mover.move_file(src, dest_dir)
        assert res.ok is False
        assert res.code == ErrorCode.SRC_VANISHED


def test_publish_non_exdev_oserrors(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class ErrorFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return True

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            raise OSError(errno.EACCES, "Permission denied")

    # rename_noreplace raises EACCES, should fall through to link_unlink
    mover = SafeMover(fs=ErrorFs())
    res = mover.move_file(src, dest_dir)
    assert res.ok is True
    assert res.strategy == MoveStrategy.LINK_UNLINK


def test_strategy_b_publish_skip_race(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    class CrossPublishRaceFs:
        def __getattr__(self, name: str) -> Any:
            return getattr(fsops, name)

        def same_device(self, a: Path, b: Path) -> bool:
            return False

        def rename_noreplace(self, s: Path, d: Path) -> bool:
            # Inject race file right at publish
            d.write_text("race winner")
            return False

    mover = SafeMover(fs=CrossPublishRaceFs())
    res = mover.move_file(src, dest_dir, collision=CollisionPolicy.SKIP)
    assert res.ok is False
    assert res.code == ErrorCode.COLLISION


def test_create_leaf_not_relative_to_static_root(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    static_root = tmp_path / "root1"
    static_root.mkdir()
    dest_dir = tmp_path / "root2" / "leaf"

    # root2 does not exist, so parent missing
    mover = SafeMover()
    res = mover.move_file(src, dest_dir, create=CreateDestinations.LEAF, static_root=static_root)
    assert res.ok is False
    assert res.code == ErrorCode.DEST_MISSING


# --------------------------------------------------------------------------- Awkward names
def test_awkward_names_spaces_and_unicode(tmp_path: Path) -> None:
    mover = SafeMover()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    # Spaces and unicode
    name = "My Report - 漢字 résumé (final).pdf"
    src = tmp_path / name
    src.write_text("report content")

    res = mover.move_file(src, dest_dir)
    assert res.ok is True
    assert (dest_dir / name).read_text() == "report content"


def test_awkward_names_leading_dash(tmp_path: Path) -> None:
    mover = SafeMover()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    name = "-rf_dangerous_looking_name.txt"
    src = tmp_path / name
    src.write_text("safe")

    res = mover.move_file(src, dest_dir)
    assert res.ok is True
    assert (dest_dir / name).read_text() == "safe"


def test_awkward_names_255_bytes(tmp_path: Path) -> None:
    mover = SafeMover()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    name = "a" * 251 + ".txt"  # exactly 255 bytes
    src = tmp_path / name
    src.write_text("long name content")

    res = mover.move_file(src, dest_dir)
    assert res.ok is True
    assert (dest_dir / name).read_text() == "long name content"


def test_awkward_names_non_utf8(tmp_path: Path) -> None:
    mover = SafeMover()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    raw_bytes = b"non_utf8_\xff\xfe_data.bin"
    decoded_name = os.fsdecode(raw_bytes)
    src = tmp_path / decoded_name
    src.write_bytes(b"non utf8 bytes")

    res = mover.move_file(src, dest_dir)
    assert res.ok is True
    assert (dest_dir / decoded_name).read_bytes() == b"non utf8 bytes"


# --------------------------------------------------------------------------- Safety Invariants
def test_S3_never_overwrites_existing_file(tmp_path: Path) -> None:
    """S3: No operation overwrites an existing path."""
    mover = SafeMover()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    target = dest_dir / "target.txt"
    target.write_text("ORIGINAL DO NOT OVERWRITE")

    src = tmp_path / "target.txt"
    src.write_text("INCOMING MALICIOUS")

    # Policy SKIP refuses
    res = mover.move_file(src, dest_dir, collision=CollisionPolicy.SKIP)
    assert res.ok is False
    assert target.read_text() == "ORIGINAL DO NOT OVERWRITE"
    assert src.read_text() == "INCOMING MALICIOUS"


def test_S4_source_removed_only_after_verified_publish(tmp_path: Path) -> None:
    """S4: The only unlink of user data is the source after a verified publish."""
    src = tmp_path / "source.txt"
    src.write_text("source file")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    mover = SafeMover()
    res = mover.move_file(src, dest_dir)
    assert res.ok is True
    assert not src.exists()
    assert (dest_dir / "source.txt").read_text() == "source file"


def test_S8_only_regular_files_moved(tmp_path: Path) -> None:
    """S8: Only regular files are moved. Symlinks are not followed."""
    mover = SafeMover()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    real_file = tmp_path / "real.txt"
    real_file.write_text("real")
    sym = tmp_path / "sym.txt"
    sym.symlink_to(real_file)

    res = mover.move_file(sym, dest_dir)
    assert res.ok is False
    assert res.code == ErrorCode.SRC_NOT_REGULAR
    assert sym.is_symlink()
    assert real_file.exists()


def test_S14_fs_lock_prevents_concurrent_moves(tmp_path: Path) -> None:
    """S14: fs_lock protects operations from concurrency."""
    lock_file = tmp_path / "mover.lock"
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    file_a = tmp_path / "file_a.txt"
    file_b = tmp_path / "file_b.txt"
    file_a.write_text("a")
    file_b.write_text("b")

    order: list[str] = []

    def move_with_lock(f: Path, label: str) -> None:
        with fs_lock(lock_file):
            order.append(f"start_{label}")
            mover = SafeMover()
            mover.move_file(f, dest_dir)
            order.append(f"end_{label}")

    t1 = threading.Thread(target=move_with_lock, args=(file_a, "A"))
    t2 = threading.Thread(target=move_with_lock, args=(file_b, "B"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Must not interleave: either A finishes before B starts, or B finishes before A starts
    assert order == ["start_A", "end_A", "start_B", "end_B"] or order == [
        "start_B",
        "end_B",
        "start_A",
        "end_A",
    ]


def test_S15_one_failure_does_not_affect_others(tmp_path: Path) -> None:
    """S15: A failing file never aborts the batch or corrupts others."""
    mover = SafeMover()
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    bad_src = tmp_path / "missing.txt"
    good_src = tmp_path / "good.txt"
    good_src.write_text("good")

    res_bad = mover.move_file(bad_src, dest_dir)
    assert res_bad.ok is False
    assert res_bad.code == ErrorCode.SRC_VANISHED

    res_good = mover.move_file(good_src, dest_dir)
    assert res_good.ok is True
    assert (dest_dir / "good.txt").read_text() == "good"


# --------------------------------------------------------------------------- Hypothesis Test
@given(
    data=st.binary(min_size=0, max_size=50000),
    filename=st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs", "Cc"),
            blacklist_characters="/\x00",
        ),
        min_size=1,
        max_size=30,
    ),
)
@settings(max_examples=25, deadline=None)
def test_hypothesis_move_preserves_content(data: bytes, filename: str) -> None:
    """Hypothesis: for random content, any successful move preserves file content."""
    # Filter invalid filenames
    if filename in (".", "..") or not filename.strip():
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        src = root / filename
        dest_dir = root / "dest"
        dest_dir.mkdir()

        try:
            src.write_bytes(data)
        except OSError:
            return  # OS rejected filename (e.g. invalid encoding)

        mover = SafeMover()
        res = mover.move_file(src, dest_dir)
        if res.ok:
            assert res.dst is not None
            assert res.dst.read_bytes() == data
            assert not src.exists()
