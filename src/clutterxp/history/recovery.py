"""Crash recovery service: reconciles pending journal rows on startup or doctor."""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from clutterxp.interfaces import History
from clutterxp.models import MoveResult, MoveStrategy, OperationRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryAction:
    """Outcome of reconciling a single pending operation."""

    op_id: int
    src_path: Path
    dst_path: Path | None
    action_taken: str  # "marked_failed" | "marked_done" | "conflict_warning"
    message: str


def _path_to_str(p: Path | None) -> str:
    """Safely format a Path to string, escaping non-UTF-8 surrogates for SQLite."""
    if p is None:
        return ""
    return os.fsencode(p).decode("utf-8", errors="backslashreplace")


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


def _find_matching_dest(
    op: OperationRecord,
    planned_dirs: Sequence[Path],
) -> Path | None:
    """Search candidate directories for a single file matching op's name, size, and mtime_ns."""
    matches: list[Path] = []
    for d in planned_dirs:
        if not d.is_dir():
            continue
        try:
            if d.resolve() == op.src_path.parent.resolve():
                continue
        except OSError:
            pass
        candidate = d / op.src_path.name
        try:
            if candidate.resolve() == op.src_path.resolve():
                continue
            st = candidate.lstat()
            if not stat.S_ISREG(st.st_mode):
                continue
            if op.size is not None and st.st_size != op.size:
                continue
            if op.mtime_ns is not None and st.st_mtime_ns != op.mtime_ns:
                continue
            matches.append(candidate)
        except (FileNotFoundError, OSError):
            continue
    if len(matches) == 1:
        return matches[0]
    return None


def _reconcile_single_op(
    history: History,
    op: OperationRecord,
    planned_dirs: Sequence[Path] | None = None,
) -> RecoveryAction:
    """Examine filesystem state for a single pending op and reconcile its status."""
    dst_path = op.dst_path

    # Case 5: dst_path is None (unknown destination)
    if dst_path is None:
        search_dirs: list[Path] = list(planned_dirs) if planned_dirs else []
        matched = _find_matching_dest(op, search_dirs) if search_dirs else None
        if matched is not None:
            dst_path = matched
        else:
            # Destination not found in planned directories
            src_exists = os.path.lexists(op.src_path)
            msg = "rolled back: not moved" if src_exists else "source and destination missing"
            history.finish_op(
                op.id,
                MoveResult(
                    ok=False,
                    src=op.src_path,
                    dst=None,
                    strategy=None,
                    message=msg,
                ),
            )
            history.record_event(
                kind="recovery",
                message=msg,
                level="info",
                batch_id=op.batch_id,
                op_id=op.id,
                path=op.src_path,
            )
            return RecoveryAction(
                op_id=op.id,
                src_path=op.src_path,
                dst_path=None,
                action_taken="marked_failed",
                message=msg,
            )

    src_exists = os.path.lexists(op.src_path)
    dst_exists = os.path.lexists(dst_path) if dst_path is not None else False
    src_display = _path_to_str(op.src_path)
    dst_display = _path_to_str(dst_path)

    # Case 1: src exists, dst missing -> crashed before move
    if src_exists and not dst_exists:
        msg = "rolled back: not moved"
        history.finish_op(
            op.id,
            MoveResult(
                ok=False,
                src=op.src_path,
                dst=dst_path,
                strategy=None,
                message=msg,
            ),
        )
        history.record_event(
            kind="recovery",
            message=msg,
            level="info",
            batch_id=op.batch_id,
            op_id=op.id,
            path=op.src_path,
        )
        return RecoveryAction(
            op_id=op.id,
            src_path=op.src_path,
            dst_path=dst_path,
            action_taken="marked_failed",
            message=msg,
        )

    # Case 2: src missing, dst exists -> crashed after move
    if not src_exists and dst_exists:
        size_matches = False
        if dst_path is not None:
            try:
                dst_st = dst_path.lstat()
                size_matches = op.size is None or dst_st.st_size == op.size
            except OSError:
                size_matches = False

        if size_matches:
            msg = f"completed: destination verified at {dst_display}"
            strat = op.strategy or MoveStrategy.RENAME_NOREPLACE
            history.finish_op(
                op.id,
                MoveResult(
                    ok=True,
                    src=op.src_path,
                    dst=dst_path,
                    strategy=strat,
                    message="",
                ),
            )
            history.record_event(
                kind="recovery",
                message=f"Completed move: {src_display} -> {dst_display}",
                level="info",
                batch_id=op.batch_id,
                op_id=op.id,
                path=dst_path,
            )
            return RecoveryAction(
                op_id=op.id,
                src_path=op.src_path,
                dst_path=dst_path,
                action_taken="marked_done",
                message=msg,
            )

        # Destination size does not match recorded size
        msg = f"destination size mismatch at {dst_display}"
        history.finish_op(
            op.id,
            MoveResult(
                ok=False,
                src=op.src_path,
                dst=dst_path,
                strategy=None,
                message=msg,
            ),
        )
        history.record_event(
            kind="recovery",
            message=msg,
            level="error",
            batch_id=op.batch_id,
            op_id=op.id,
            path=dst_path,
        )
        return RecoveryAction(
            op_id=op.id,
            src_path=op.src_path,
            dst_path=dst_path,
            action_taken="marked_failed",
            message=msg,
        )

    # Case 3: src exists, dst exists -> crashed between publish and unlink (cross-FS)
    # NEVER delete either copy; mark failed and warn loudly with both paths.
    if src_exists and dst_exists:
        msg = (
            f"both copies exist, remove one manually: source {src_display}, "
            f"destination {dst_display}"
        )
        history.finish_op(
            op.id,
            MoveResult(
                ok=False,
                src=op.src_path,
                dst=dst_path,
                strategy=op.strategy or MoveStrategy.COPY_VERIFY,
                message=msg,
            ),
        )
        history.record_event(
            kind="recovery",
            message=msg,
            level="warning",
            batch_id=op.batch_id,
            op_id=op.id,
            path=op.src_path,
        )
        return RecoveryAction(
            op_id=op.id,
            src_path=op.src_path,
            dst_path=dst_path,
            action_taken="conflict_warning",
            message=msg,
        )

    # Case 4: neither exists -> removed/moved by external entity
    msg = "source and destination missing"
    history.finish_op(
        op.id,
        MoveResult(
            ok=False,
            src=op.src_path,
            dst=dst_path,
            strategy=None,
            message=msg,
        ),
    )
    history.record_event(
        kind="recovery",
        message=msg,
        level="info",
        batch_id=op.batch_id,
        op_id=op.id,
        path=op.src_path,
    )
    return RecoveryAction(
        op_id=op.id,
        src_path=op.src_path,
        dst_path=dst_path,
        action_taken="marked_failed",
        message=msg,
    )


def reconcile(
    history: History,
    now: str | None = None,
    planned_dirs: Sequence[Path] | None = None,
) -> list[RecoveryAction]:
    """Examines all pending operations per docs/05 section 5 recovery table."""
    pending = history.pending_ops()
    actions: list[RecoveryAction] = []
    for op in pending:
        actions.append(_reconcile_single_op(history, op, planned_dirs=planned_dirs))
    return actions


def reconcile_on_startup(
    history: History,
    startup_time_iso: str,
    planned_dirs: Sequence[Path] | None = None,
) -> list[RecoveryAction]:
    """Reconciles only pending ops created strictly before startup_time_iso."""
    startup_dt = _parse_iso(startup_time_iso)
    pending = history.pending_ops()
    actions: list[RecoveryAction] = []
    for op in pending:
        op_dt = _parse_iso(op.created_at)
        if op_dt < startup_dt:
            actions.append(_reconcile_single_op(history, op, planned_dirs=planned_dirs))
    return actions


__all__ = ["RecoveryAction", "reconcile", "reconcile_on_startup"]
