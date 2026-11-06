"""Test doubles and helpers for ClutterXP test suites.

Provides in-memory implementations of the frozen Protocols from interfaces.py,
injectable fake clock, and polling wait helper.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from clutterxp.interfaces import History, Mover, RuleEngine
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    Decision,
    DecisionKind,
    ErrorCode,
    FileInfo,
    MoveResult,
    MoveStrategy,
    OperationRecord,
    OpStatus,
    ProgressCb,
    RuleTrace,
)


class FakeClock:
    """Injectable monotonic clock for Debouncer/Settler testing."""

    def __init__(self, initial_time: float = 0.0) -> None:
        self._current_time: float = float(initial_time)

    def __call__(self) -> float:
        return self._current_time

    def time(self) -> float:
        return self._current_time

    def advance(self, seconds: float = 1.0) -> float:
        if seconds < 0:
            raise ValueError(f"Cannot advance clock backwards: {seconds}")
        self._current_time += seconds
        return self._current_time

    def set(self, timestamp: float) -> None:
        if timestamp < self._current_time:
            raise ValueError(f"Cannot set clock backwards: {timestamp} < {self._current_time}")
        self._current_time = float(timestamp)


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.05,
    message: str | None = None,
) -> None:
    """Poll predicate every interval seconds until True or raise TimeoutError."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            msg = message or f"wait_until timed out after {timeout}s"
            raise TimeoutError(msg)
        time.sleep(interval)


@dataclass(frozen=True, slots=True)
class DecideCall:
    file: FileInfo
    folder: Path | None


class FakeRuleEngine(RuleEngine):
    """In-memory, deterministic RuleEngine implementation."""

    def __init__(
        self,
        decisions: dict[str, Decision] | None = None,
        *,
        fallback_decision: Decision | None = None,
        traces: dict[str, Sequence[RuleTrace]] | None = None,
    ) -> None:
        self.decisions: dict[str, Decision] = dict(decisions) if decisions is not None else {}
        self.fallback_decision: Decision | None = fallback_decision
        self.traces: dict[str, Sequence[RuleTrace]] = dict(traces) if traces is not None else {}
        self.decide_calls: list[DecideCall] = []
        self.trace_calls: list[FileInfo] = []

    def decide(self, file: FileInfo, *, folder: Path | None = None) -> Decision:
        self.decide_calls.append(DecideCall(file=file, folder=folder))
        # Look up by filename, then full string path
        if file.name in self.decisions:
            dec = self.decisions[file.name]
            if dec.file != file:
                return Decision(
                    file=file,
                    kind=dec.kind,
                    hit=dec.hit,
                    candidates=dec.candidates,
                    reason=dec.reason,
                )
            return dec
        if str(file.path) in self.decisions:
            dec = self.decisions[str(file.path)]
            if dec.file != file:
                return Decision(
                    file=file,
                    kind=dec.kind,
                    hit=dec.hit,
                    candidates=dec.candidates,
                    reason=dec.reason,
                )
            return dec
        if self.fallback_decision is not None:
            fb = self.fallback_decision
            if fb.file != file:
                return Decision(
                    file=file,
                    kind=fb.kind,
                    hit=fb.hit,
                    candidates=fb.candidates,
                    reason=fb.reason,
                )
            return fb
        return Decision(file=file, kind=DecisionKind.NO_MATCH, reason="no rule matched")

    def trace(self, file: FileInfo) -> Sequence[RuleTrace]:
        self.trace_calls.append(file)
        return self.traces.get(file.name, ())


@dataclass(frozen=True, slots=True)
class MoverCall:
    src: Path
    dest_dir: Path
    new_name: str | None
    collision: CollisionPolicy
    create: CreateDestinations
    static_root: Path | None
    expect_size: int | None
    expect_mtime_ns: int | None


class FakeMover(Mover):
    """In-memory Mover implementation that records calls without modifying filesystem."""

    def __init__(
        self,
        *,
        default_strategy: MoveStrategy = MoveStrategy.RENAME_NOREPLACE,
        failure_code: ErrorCode | None = None,
        failure_message: str = "Simulated mover failure",
        fail_predicate: Callable[[Path], bool] | None = None,
    ) -> None:
        self.default_strategy: MoveStrategy = default_strategy
        self.failure_code: ErrorCode | None = failure_code
        self.failure_message: str = failure_message
        self.fail_predicate: Callable[[Path], bool] | None = fail_predicate
        self.calls: list[MoverCall] = []

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
    ) -> MoveResult:
        call = MoverCall(
            src=src,
            dest_dir=dest_dir,
            new_name=new_name,
            collision=collision,
            create=create,
            static_root=static_root,
            expect_size=expect_size,
            expect_mtime_ns=expect_mtime_ns,
        )
        self.calls.append(call)

        should_fail = (self.failure_code is not None) or (
            self.fail_predicate is not None and self.fail_predicate(src)
        )
        if should_fail:
            code = self.failure_code or ErrorCode.UNKNOWN
            return MoveResult(
                ok=False,
                src=src,
                dst=None,
                strategy=None,
                code=code,
                message=self.failure_message,
            )

        dst_name = new_name or src.name
        dst = dest_dir / dst_name

        if progress is not None:
            size = expect_size or 0
            progress(size, size)

        return MoveResult(
            ok=True,
            src=src,
            dst=dst,
            strategy=self.default_strategy,
            code=ErrorCode.NONE,
            message="",
            created_dirs=(),
            renamed_due_to_collision=False,
            bytes_copied=expect_size or 0,
        )


@dataclass(frozen=True, slots=True)
class HistoryEvent:
    kind: str
    message: str
    level: str
    batch_id: int | None
    op_id: int | None
    path: Path | None


@dataclass(frozen=True, slots=True)
class BatchRecord:
    batch_id: int
    command: str
    folder: Path | None
    note: str | None
    created_at: str


class FakeHistory(History):
    """In-memory History implementation storing operations and events."""

    def __init__(self) -> None:
        self._batch_counter: int = 0
        self._op_counter: int = 0
        self.batches: dict[int, BatchRecord] = {}
        self.ops: dict[int, OperationRecord] = {}
        self.events: list[HistoryEvent] = []

    def begin_batch(
        self,
        command: str,
        folder: Path | None = None,
        note: str | None = None,
    ) -> int:
        self._batch_counter += 1
        batch_id = self._batch_counter
        now_iso = datetime.now(timezone.utc).isoformat()
        self.batches[batch_id] = BatchRecord(
            batch_id=batch_id,
            command=command,
            folder=folder,
            note=note,
            created_at=now_iso,
        )
        return batch_id

    def begin_op(
        self,
        batch_id: int,
        src: Path,
        rule_name: str | None,
        file: FileInfo,
    ) -> int:
        """Insert status=pending BEFORE any filesystem change (journal-first per S5)."""
        self._op_counter += 1
        op_id = self._op_counter
        now_iso = datetime.now(timezone.utc).isoformat()
        op = OperationRecord(
            id=op_id,
            batch_id=batch_id,
            status=OpStatus.PENDING,
            src_path=src,
            dst_path=None,
            rule_name=rule_name,
            strategy=None,
            size=file.size,
            mtime_ns=file.mtime_ns,
            inode=file.inode,
            device=file.device,
            sha256=None,
            created_dirs=(),
            created_at=now_iso,
            finished_at=None,
            undone_at=None,
            error=None,
        )
        self.ops[op_id] = op
        return op_id

    def finish_op(self, op_id: int, result: MoveResult) -> None:
        """Mark done/failed with final dst, strategy and created_dirs."""
        old = self.ops.get(op_id)
        if old is None:
            raise KeyError(f"Operation id {op_id} not found")
        now_iso = datetime.now(timezone.utc).isoformat()
        status = OpStatus.DONE if result.ok else OpStatus.FAILED
        error = None if result.ok else (result.message or str(result.code))
        self.ops[op_id] = OperationRecord(
            id=old.id,
            batch_id=old.batch_id,
            status=status,
            src_path=old.src_path,
            dst_path=result.dst,
            rule_name=old.rule_name,
            strategy=result.strategy,
            size=old.size,
            mtime_ns=old.mtime_ns,
            inode=old.inode,
            device=old.device,
            sha256=old.sha256,
            created_dirs=result.created_dirs,
            created_at=old.created_at,
            finished_at=now_iso,
            undone_at=old.undone_at,
            error=error,
        )

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
        self.events.append(
            HistoryEvent(
                kind=kind,
                message=message,
                level=level,
                batch_id=batch_id,
                op_id=op_id,
                path=path,
            )
        )

    def get_op(self, op_id: int) -> OperationRecord | None:
        return self.ops.get(op_id)

    def list_ops(
        self,
        *,
        batch_id: int | None = None,
        status: OpStatus | None = None,
        limit: int = 50,
        since_iso: str | None = None,
        rule_name: str | None = None,
        path_contains: str | None = None,
    ) -> Sequence[OperationRecord]:
        matches: list[OperationRecord] = []
        for op in self.ops.values():
            if batch_id is not None and op.batch_id != batch_id:
                continue
            if status is not None and op.status != status:
                continue
            if since_iso is not None and op.created_at < since_iso:
                continue
            if rule_name is not None and op.rule_name != rule_name:
                continue
            if path_contains is not None:
                src_match = path_contains in str(op.src_path)
                dst_match = op.dst_path is not None and path_contains in str(op.dst_path)
                if not (src_match or dst_match):
                    continue
            matches.append(op)
        return matches[:limit]

    def last_batch_id(self, *, with_status: OpStatus = OpStatus.DONE) -> int | None:
        matching_batches = [op.batch_id for op in self.ops.values() if op.status == with_status]
        return max(matching_batches) if matching_batches else None

    def mark_undone(self, op_id: int) -> None:
        old = self.ops.get(op_id)
        if old is None:
            raise KeyError(f"Operation id {op_id} not found")
        now_iso = datetime.now(timezone.utc).isoformat()
        self.ops[op_id] = OperationRecord(
            id=old.id,
            batch_id=old.batch_id,
            status=OpStatus.UNDONE,
            src_path=old.src_path,
            dst_path=old.dst_path,
            rule_name=old.rule_name,
            strategy=old.strategy,
            size=old.size,
            mtime_ns=old.mtime_ns,
            inode=old.inode,
            device=old.device,
            sha256=old.sha256,
            created_dirs=old.created_dirs,
            created_at=old.created_at,
            finished_at=old.finished_at,
            undone_at=now_iso,
            error=old.error,
        )

    def pending_ops(self) -> Sequence[OperationRecord]:
        return [op for op in self.ops.values() if op.status == OpStatus.PENDING]


__all__ = [
    "BatchRecord",
    "DecideCall",
    "FakeClock",
    "FakeHistory",
    "FakeMover",
    "FakeRuleEngine",
    "HistoryEvent",
    "MoverCall",
    "wait_until",
]
