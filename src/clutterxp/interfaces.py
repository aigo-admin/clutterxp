"""ClutterXP module interfaces (FROZEN).

These Protocols let agents build modules in parallel: each agent implements its
Protocol and tests against fakes of the others. Changing a signature requires a
CONTRACT_CHANGE request (see AGENTS.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol, Sequence, runtime_checkable

from .models import (
    BatchResult,
    CollisionPolicy,
    CreateDestinations,
    Decision,
    DuplicateGroup,
    ErrorCode,
    FileInfo,
    MoveResult,
    OperationRecord,
    OpStatus,
    Plan,
    ProgressCb,
    RuleTrace,
    UndoOutcome,
)


@runtime_checkable
class RuleEngine(Protocol):
    """Pure: no filesystem writes, no file-content reads. stat() data comes in via FileInfo."""

    def decide(self, file: FileInfo, *, folder: Path | None = None) -> Decision:
        """`folder` is the watched/scanned root; used to apply per-folder rule subsets."""

    def trace(self, file: FileInfo) -> Sequence[RuleTrace]:
        """Explain every rule's match/non-match for one file (`rules test`)."""


@runtime_checkable
class Mover(Protocol):
    """Filesystem primitive. Never deletes user data except the source after a verified publish."""

    def move_file(
        self,
        src: Path,
        dest_dir: Path,
        *,
        new_name: str | None = None,
        collision: CollisionPolicy = CollisionPolicy.SKIP,
        create: CreateDestinations = CreateDestinations.LEAF,
        static_root: Path | None = None,
        expect_size: int | None = None,
        expect_mtime_ns: int | None = None,
        progress: ProgressCb | None = None,
    ) -> MoveResult: ...


@runtime_checkable
class History(Protocol):
    def begin_batch(
        self, command: str, folder: Path | None = None, note: str | None = None
    ) -> int: ...

    def begin_op(self, batch_id: int, src: Path, rule_name: str | None, file: FileInfo) -> int:
        """Insert status=pending BEFORE any filesystem change. Must be durable on return."""

    def finish_op(self, op_id: int, result: MoveResult) -> None:
        """Mark done/failed with final dst, strategy and created_dirs."""

    def record_event(
        self,
        kind: str,
        message: str,
        *,
        level: str = "info",
        batch_id: int | None = None,
        op_id: int | None = None,
        path: Path | None = None,
    ) -> None: ...

    def get_op(self, op_id: int) -> OperationRecord | None: ...

    def list_ops(
        self,
        *,
        batch_id: int | None = None,
        status: OpStatus | None = None,
        limit: int = 50,
        since_iso: str | None = None,
        rule_name: str | None = None,
        path_contains: str | None = None,
    ) -> Sequence[OperationRecord]: ...

    def last_batch_id(self, *, with_status: OpStatus = OpStatus.DONE) -> int | None: ...

    def mark_undone(self, op_id: int) -> None: ...

    def pending_ops(self) -> Sequence[OperationRecord]: ...


@runtime_checkable
class Organizer(Protocol):
    """The single facade used by the CLI, the watcher and the GUI."""

    def plan(
        self,
        paths: Iterable[Path] | None = None,
        *,
        recursive: bool | None = None,
        only_rules: Sequence[str] | None = None,
    ) -> Plan: ...

    def apply(
        self,
        plan: Plan,
        *,
        command: str = "organize",
        progress: ProgressCb | None = None,
    ) -> BatchResult: ...

    def process_file(
        self, path: Path, *, folder: Path | None = None, command: str = "watch"
    ) -> BatchResult:
        """Single-file path used by the watcher: decide -> plan -> apply, one batch of one op."""

    def undo(
        self,
        *,
        op_id: int | None = None,
        batch_id: int | None = None,
        dry_run: bool = False,
        on_conflict: str = "skip",
        allow_modified: bool = False,
    ) -> Sequence[UndoOutcome]: ...

    def reload_config(self) -> None: ...


@runtime_checkable
class DuplicateDetector(Protocol):
    def scan(
        self,
        folders: Sequence[Path],
        *,
        recursive: bool = True,
        min_size: int = 1,
        exclude: Sequence[str] = (),
        jobs: int = 1,
        potential: bool = True,
        progress: ProgressCb | None = None,
    ) -> Sequence[DuplicateGroup]: ...


__all__ = [
    "RuleEngine",
    "Mover",
    "History",
    "Organizer",
    "DuplicateDetector",
    "ErrorCode",
]
