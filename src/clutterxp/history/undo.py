"""Undo service: safe rollback of file operations."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from clutterxp import locking
from clutterxp.interfaces import History, Mover
from clutterxp.models import (
    ClutterError,
    CollisionPolicy,
    CreateDestinations,
    ErrorCode,
    OperationRecord,
    OpStatus,
    UndoOutcome,
)
from clutterxp.mover.collision import split_name_ext

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 digest of a file in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _path_to_str(p: Path | None) -> str:
    """Safely format a Path to string, escaping non-UTF-8 surrogates for SQLite."""
    if p is None:
        return ""
    return os.fsencode(p).decode("utf-8", errors="backslashreplace")


class UndoService:
    """Safely undoes previously executed file operations.

    Adheres to safety invariants S3 (no overwrite), S4 (no delete user files),
    and S15 (independent operations: one refusal does not block the batch).
    """

    def __init__(self, history: History, mover: Mover, fs_lock_path: Path) -> None:
        """Initialize UndoService with history, mover, and lock file path."""
        self.history = history
        self.mover = mover
        self.fs_lock_path = Path(fs_lock_path)

    @staticmethod
    def _matches_file_path(target: Path | None, query: Path) -> bool:
        """Check if target destination path matches the query path."""
        if target is None:
            return False
        if target == query:
            return True
        try:
            return target.resolve() == query.resolve()
        except OSError:
            return False

    def undo(
        self,
        *,
        op_id: int | None = None,
        batch_id: int | None = None,
        file_path: Path | None = None,
        dry_run: bool = False,
        on_conflict: str = "skip",  # "skip" | "rename"
        allow_modified: bool = False,
    ) -> Sequence[UndoOutcome]:
        """Undo file operations matching the specified selector.

        Selectors are checked in priority order:
        1. op_id: single operation
        2. batch_id: all DONE operations in the batch (reverse id order)
        3. file_path: latest DONE operation where dst_path == file_path
        4. neither: all DONE operations in the last DONE batch (reverse id order)

        Args:
            op_id: Specific operation id to undo.
            batch_id: Batch id whose DONE operations should be undone.
            file_path: Current destination path of the file to undo.
            dry_run: If True, evaluate checks without modifying filesystem or history.
            on_conflict: "skip" (fail if occupied) or "rename" (restore as <stem> (restored)<ext>).
            allow_modified: If True, waive check 3 (size/mtime/hash verification).

        Returns:
            Sequence of UndoOutcome objects.
        """
        if on_conflict not in ("skip", "rename"):
            msg = f"Invalid on_conflict policy: {on_conflict!r}. Must be 'skip' or 'rename'."
            raise ValueError(msg)

        ops_to_undo: list[OperationRecord] = []

        if op_id is not None:
            op = self.history.get_op(op_id)
            if op is None:
                return [
                    UndoOutcome(
                        op_id=op_id,
                        ok=False,
                        code=ErrorCode.UNDO_NOT_DONE,
                        message=f"Operation {op_id} not found",
                    )
                ]
            ops_to_undo = [op]
        elif batch_id is not None:
            ops = self.history.list_ops(batch_id=batch_id, status=OpStatus.DONE, limit=100_000)
            ops_to_undo = sorted(ops, key=lambda o: o.id, reverse=True)
        elif file_path is not None:
            done_ops = self.history.list_ops(status=OpStatus.DONE, limit=100_000)
            matching = [op for op in done_ops if self._matches_file_path(op.dst_path, file_path)]
            if not matching:
                return []
            latest_op = max(matching, key=lambda o: o.id)
            ops_to_undo = [latest_op]
        else:
            last_bid = self.history.last_batch_id(with_status=OpStatus.DONE)
            if last_bid is None:
                return []
            ops = self.history.list_ops(batch_id=last_bid, status=OpStatus.DONE, limit=100_000)
            ops_to_undo = sorted(ops, key=lambda o: o.id, reverse=True)

        if not ops_to_undo:
            return []

        if not dry_run:
            self.history.begin_batch("undo", note=f"Undo {len(ops_to_undo)} operations")

        outcomes: list[UndoOutcome] = []
        for op in ops_to_undo:
            if dry_run:
                outcome = self._undo_single_op(
                    op,
                    dry_run=True,
                    on_conflict=on_conflict,
                    allow_modified=allow_modified,
                )
            else:
                try:
                    with locking.fs_lock(self.fs_lock_path):
                        outcome = self._undo_single_op(
                            op,
                            dry_run=False,
                            on_conflict=on_conflict,
                            allow_modified=allow_modified,
                        )
                except ClutterError as e:
                    outcome = UndoOutcome(
                        op_id=op.id,
                        ok=False,
                        code=e.code,
                        message=str(e),
                    )
            outcomes.append(outcome)

        return outcomes

    def _undo_single_op(
        self,
        op: OperationRecord,
        *,
        dry_run: bool,
        on_conflict: str,
        allow_modified: bool,
    ) -> UndoOutcome:
        """Run checks 1-5 and optionally execute the restore for a single operation."""
        # Check 1: status is DONE
        if op.status != OpStatus.DONE:
            return UndoOutcome(
                op_id=op.id,
                ok=False,
                code=ErrorCode.UNDO_NOT_DONE,
                message=(
                    f"Operation {op.id} has status {op.status.value}, "
                    f"expected {OpStatus.DONE.value}"
                ),
            )

        # Check 2: dst_path exists and is a regular file
        if op.dst_path is None:
            return UndoOutcome(
                op_id=op.id,
                ok=False,
                code=ErrorCode.UNDO_DEST_MISSING,
                message=f"Operation {op.id} destination path is missing",
            )

        try:
            dst_st = op.dst_path.lstat()
        except (FileNotFoundError, OSError):
            return UndoOutcome(
                op_id=op.id,
                ok=False,
                code=ErrorCode.UNDO_DEST_MISSING,
                message=f"Destination {_path_to_str(op.dst_path)} does not exist",
            )

        if not stat.S_ISREG(dst_st.st_mode):
            return UndoOutcome(
                op_id=op.id,
                ok=False,
                code=ErrorCode.UNDO_DEST_MISSING,
                message=f"Destination {_path_to_str(op.dst_path)} is not a regular file",
            )

        # Check 3: lstat(dst) size == op.size and mtime_ns == op.mtime_ns (and sha256)
        size_mismatch = op.size is not None and dst_st.st_size != op.size
        mtime_mismatch = op.mtime_ns is not None and dst_st.st_mtime_ns != op.mtime_ns
        sha_mismatch = False
        if op.sha256 is not None:
            try:
                cur_sha = _sha256_file(op.dst_path)
                if cur_sha != op.sha256:
                    sha_mismatch = True
            except OSError:
                sha_mismatch = True

        if (size_mismatch or mtime_mismatch or sha_mismatch) and not allow_modified:
            details: list[str] = []
            if size_mismatch:
                details.append(f"size {dst_st.st_size} != recorded {op.size}")
            if mtime_mismatch:
                details.append(f"mtime_ns {dst_st.st_mtime_ns} != recorded {op.mtime_ns}")
            if sha_mismatch:
                details.append("hash mismatch")
            dst_name = _path_to_str(op.dst_path)
            return UndoOutcome(
                op_id=op.id,
                ok=False,
                code=ErrorCode.UNDO_DEST_CHANGED,
                message=f"Destination file {dst_name} has been modified ({', '.join(details)})",
            )

        # Check 4: original src_path does not exist
        src_exists = os.path.lexists(op.src_path)
        if src_exists:
            if on_conflict == "skip":
                return UndoOutcome(
                    op_id=op.id,
                    ok=False,
                    code=ErrorCode.UNDO_ORIGIN_OCCUPIED,
                    message=f"Original origin {_path_to_str(op.src_path)} is occupied",
                )
            # on_conflict == "rename"
            stem, ext = split_name_ext(op.src_path.name)
            target_name = f"{stem} (restored){ext}"
        else:
            target_name = op.src_path.name

        target_parent = op.src_path.parent
        target_path = target_parent / target_name

        # Check 5: original parent directory exists / is writable
        if target_parent.exists():
            if not target_parent.is_dir():
                return UndoOutcome(
                    op_id=op.id,
                    ok=False,
                    code=ErrorCode.DEST_NOT_WRITABLE,
                    message=(
                        f"Parent path {_path_to_str(target_parent)} exists but is not a directory"
                    ),
                )
            if not os.access(target_parent, os.W_OK | os.X_OK):
                return UndoOutcome(
                    op_id=op.id,
                    ok=False,
                    code=ErrorCode.DEST_NOT_WRITABLE,
                    message=f"Parent directory {_path_to_str(target_parent)} is not writable",
                )
        else:
            curr = target_parent
            while not curr.exists() and curr.parent != curr:
                curr = curr.parent
            if not curr.exists() or not curr.is_dir() or not os.access(curr, os.W_OK | os.X_OK):
                return UndoOutcome(
                    op_id=op.id,
                    ok=False,
                    code=ErrorCode.DEST_NOT_WRITABLE,
                    message=(
                        f"Cannot recreate parent directory {_path_to_str(target_parent)}: "
                        f"ancestor {_path_to_str(curr)} is not writable"
                    ),
                )

        # Dry-run evaluation: return outcome without any filesystem or history mutations
        if dry_run:
            if os.path.lexists(target_path):
                return UndoOutcome(
                    op_id=op.id,
                    ok=False,
                    code=ErrorCode.COLLISION,
                    message=f"Restored destination {_path_to_str(target_path)} already exists",
                )
            return UndoOutcome(
                op_id=op.id,
                ok=True,
                restored_to=target_path,
                code=ErrorCode.NONE,
                message=(
                    f"Would restore {_path_to_str(op.dst_path)} to {_path_to_str(target_path)}"
                ),
            )

        # Execute restore
        parent_was_missing = not target_parent.exists()

        move_res = self.mover.move_file(
            op.dst_path,
            target_parent,
            new_name=target_name,
            collision=CollisionPolicy.SKIP,
            create=CreateDestinations.ALL,
        )

        if not move_res.ok:
            return UndoOutcome(
                op_id=op.id,
                ok=False,
                code=move_res.code,
                message=move_res.message,
            )

        effective_restored_to = move_res.dst or target_path
        dst_display = _path_to_str(op.dst_path)
        restored_display = _path_to_str(effective_restored_to)

        if parent_was_missing:
            self.history.record_event(
                "info",
                f"Recreated missing destination directory {_path_to_str(target_parent)}",
                op_id=op.id,
                batch_id=op.batch_id,
                path=target_parent,
            )

        self.history.mark_undone(op.id)
        self.history.record_event(
            "undo",
            f"Restored {dst_display} to {restored_display}",
            op_id=op.id,
            batch_id=op.batch_id,
        )

        # Clean up op.created_dirs ONLY if empty, deepest first
        if op.created_dirs:
            sorted_dirs = sorted(op.created_dirs, key=lambda p: len(p.parts), reverse=True)
            for d in sorted_dirs:
                with contextlib.suppress(OSError):
                    os.rmdir(d)  # noqa: PTH106

        return UndoOutcome(
            op_id=op.id,
            ok=True,
            restored_to=effective_restored_to,
        )


__all__ = ["UndoService"]
