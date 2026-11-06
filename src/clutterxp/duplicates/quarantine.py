"""Duplicate quarantine service for ClutterXP (v1.1).

Moves exact duplicates into a designated quarantine directory using Mover and History,
ensuring every operation is non-destructive, recorded in the journal, and undoable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from clutterxp import locking, paths, safety
from clutterxp.interfaces import History, Mover
from clutterxp.models import (
    BatchResult,
    CollisionPolicy,
    CreateDestinations,
    DuplicateGroup,
    ErrorCode,
    FileInfo,
    MoveResult,
)


@dataclass(frozen=True, slots=True)
class QuarantineAction:
    file: FileInfo
    target_dir: Path
    target_name: str
    group_sha256: str | None


@dataclass(frozen=True, slots=True)
class QuarantinePlan:
    quarantine_dir: Path
    keep_strategy: str
    kept_files: tuple[FileInfo, ...]
    actions: tuple[QuarantineAction, ...]

    @property
    def total_moves(self) -> int:
        return len(self.actions)

    @property
    def total_bytes(self) -> int:
        return sum(a.file.size for a in self.actions)


def select_kept_file(
    files: Sequence[FileInfo],
    strategy: str,
    keep_path: Path | None = None,
) -> FileInfo:
    """Select which file to keep according to strategy."""
    if not files:
        raise ValueError("Cannot select kept file from empty file list")

    if keep_path is not None:
        target_resolved = keep_path.resolve()
        for f in files:
            if f.path.resolve() == target_resolved:
                return f
        raise ValueError(f"keep_path '{keep_path}' is not present in duplicate group")

    if strategy == "oldest":
        return min(files, key=lambda f: (f.mtime_ns, str(f.path)))
    elif strategy == "newest":
        return max(files, key=lambda f: (f.mtime_ns, str(f.path)))
    elif strategy == "shortest-path":
        return min(files, key=lambda f: (len(str(f.path)), str(f.path)))
    else:
        raise ValueError(
            f"Unknown keep strategy: {strategy!r}. "
            "Must be 'oldest', 'newest', 'shortest-path', or specify keep_path."
        )


def plan_quarantine(
    groups: Sequence[DuplicateGroup],
    quarantine_dir: Path,
    *,
    keep_strategy: str = "oldest",
    keep_path: Path | None = None,
) -> QuarantinePlan:
    """Create a quarantine plan for exact duplicate groups."""
    safety.check_destination(quarantine_dir / "test.tmp")

    kept: list[FileInfo] = []
    actions: list[QuarantineAction] = []

    for group in groups:
        if group.kind != "exact" or len(group.files) < 2:
            continue

        chosen_keep = select_kept_file(group.files, keep_strategy, keep_path=keep_path)
        kept.append(chosen_keep)

        for f in group.files:
            if f == chosen_keep:
                continue

            actions.append(
                QuarantineAction(
                    file=f,
                    target_dir=quarantine_dir,
                    target_name=f.name,
                    group_sha256=group.sha256,
                )
            )

    return QuarantinePlan(
        quarantine_dir=quarantine_dir,
        keep_strategy=keep_strategy if keep_path is None else f"path:{keep_path}",
        kept_files=tuple(kept),
        actions=tuple(actions),
    )


def apply_quarantine(
    plan: QuarantinePlan,
    mover: Mover,
    history: History,
    *,
    fs_lock_path: Path | None = None,
) -> BatchResult:
    """Execute a quarantine plan under journal-first protocol and fs lock."""
    lock_file = fs_lock_path if fs_lock_path is not None else paths.fs_lock_path()
    batch_id = history.begin_batch("duplicates quarantine", folder=plan.quarantine_dir)

    moved = 0
    skipped = 0
    blocked = 0
    failed = 0
    results: list[MoveResult] = []

    with locking.fs_lock(lock_file):
        for action in plan.actions:
            # Check if source file vanished or changed
            if not action.file.path.exists():
                skipped += 1
                res = MoveResult(
                    ok=False,
                    src=action.file.path,
                    dst=None,
                    strategy=None,
                    code=ErrorCode.SRC_VANISHED,
                    message="File vanished before quarantine",
                )
                results.append(res)
                continue

            # Record durable pending op in journal before move (Invariant S5)
            op_id = history.begin_op(
                batch_id,
                action.file.path,
                "quarantine",
                action.file,
            )

            # Move file using SafeMover (rename on collision, create dir)
            res = mover.move_file(
                action.file.path,
                action.target_dir,
                new_name=action.target_name,
                collision=CollisionPolicy.RENAME,
                create=CreateDestinations.ALL,
                expect_size=action.file.size,
                expect_mtime_ns=action.file.mtime_ns,
            )

            history.finish_op(op_id, res)
            results.append(res)

            if res.ok:
                moved += 1
            else:
                failed += 1

    return BatchResult(
        batch_id=batch_id,
        moved=moved,
        skipped=skipped,
        blocked=blocked,
        failed=failed,
        results=tuple(results),
    )


__all__ = [
    "QuarantineAction",
    "QuarantinePlan",
    "apply_quarantine",
    "plan_quarantine",
    "select_kept_file",
]
