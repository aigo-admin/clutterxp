"""Pytest fixtures and test doubles for watch service tests."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Ensure this worktree's src directory takes precedence over the shared venv editable install
src_dir = str(Path(__file__).resolve().parents[2] / "src")
if sys.path[0] != src_dir:
    if src_dir in sys.path:
        sys.path.remove(src_dir)
    sys.path.insert(0, src_dir)

import clutterxp  # noqa: E402

worktree_pkg = str(Path(src_dir) / "clutterxp")
if worktree_pkg not in clutterxp.__path__:
    clutterxp.__path__.insert(0, worktree_pkg)

from clutterxp.interfaces import Organizer  # noqa: E402
from clutterxp.models import (  # noqa: E402
    BatchResult,
    CollisionPolicy,
    CreateDestinations,
    Decision,
    DecisionKind,
    ErrorCode,
    FileInfo,
    MoveResult,
    MoveStrategy,
    Plan,
    PlannedAction,
    PlanStatus,
    ProgressCb,
    RuleHit,
    UndoOutcome,
)


@dataclass
class ProcessCall:
    path: Path
    folder: Path | None
    command: str


class FakeOrganizer(Organizer):
    """Test implementation of Organizer protocol for watch tests."""

    def __init__(
        self,
        default_dest: Path | None = None,
        *,
        rule_name: str = "TestRule",
        unmatched_names: tuple[str, ...] = ("unmatched.txt", "nomatch.doc"),
        ambiguous_names: tuple[str, ...] = ("ambiguous.txt",),
        blocked_names: tuple[str, ...] = ("blocked.txt",),
        fail_reload: bool = False,
    ) -> None:
        self.default_dest = default_dest
        self.rule_name = rule_name
        self.unmatched_names = set(unmatched_names)
        self.ambiguous_names = set(ambiguous_names)
        self.blocked_names = set(blocked_names)
        self.fail_reload = fail_reload

        self.process_calls: list[ProcessCall] = []
        self.plan_calls: list[list[Path]] = []
        self.apply_calls: list[Plan] = []
        self.reload_count: int = 0

    def process_file(
        self, path: Path, *, folder: Path | None = None, command: str = "watch"
    ) -> BatchResult:
        self.process_calls.append(ProcessCall(path=path, folder=folder, command=command))

        if path.name in self.unmatched_names:
            return BatchResult(batch_id=1, moved=0, skipped=1, blocked=0, failed=0, results=())

        if path.name in self.ambiguous_names:
            return BatchResult(batch_id=1, moved=0, skipped=1, blocked=0, failed=0, results=())

        if path.name in self.blocked_names:
            res = MoveResult(
                ok=False,
                src=path,
                dst=None,
                strategy=None,
                code=ErrorCode.COLLISION,
                message="Destination collision",
            )
            return BatchResult(batch_id=1, moved=0, skipped=0, blocked=1, failed=0, results=(res,))

        dest_dir = self.default_dest
        if dest_dir is None:
            dest_dir = path.parent / "sorted"

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / path.name

        try:
            shutil.move(str(path), str(dest_path))
            res = MoveResult(
                ok=True,
                src=path,
                dst=dest_path,
                strategy=MoveStrategy.RENAME_NOREPLACE,
                code=ErrorCode.NONE,
                message=self.rule_name,
            )
            return BatchResult(batch_id=1, moved=1, skipped=0, blocked=0, failed=0, results=(res,))
        except Exception as e:
            res = MoveResult(
                ok=False,
                src=path,
                dst=None,
                strategy=None,
                code=ErrorCode.UNKNOWN,
                message=str(e),
            )
            return BatchResult(batch_id=1, moved=0, skipped=0, blocked=0, failed=1, results=(res,))

    def plan(
        self,
        paths: Iterable[Path] | None = None,
        *,
        recursive: bool | None = None,
        only_rules: Sequence[str] | None = None,
    ) -> Plan:
        path_list = list(paths) if paths is not None else []
        self.plan_calls.append(path_list)

        actions: list[PlannedAction] = []
        dest_dir = self.default_dest or Path("/tmp/sorted")

        for p in path_list:
            if p.is_dir() and recursive:
                for root, _, files in os.walk(p):
                    for f in files:
                        actions.append(self._make_action(Path(root) / f, dest_dir))
            elif p.is_dir():
                for item in p.iterdir():
                    if item.is_file():
                        actions.append(self._make_action(item, dest_dir))
            else:
                actions.append(self._make_action(p, dest_dir))

        return Plan(folders=tuple(path_list), actions=tuple(actions))

    def _make_action(self, file_path: Path, dest_dir: Path) -> PlannedAction:
        try:
            fi = FileInfo.from_path(file_path)
        except OSError:
            fi = FileInfo(
                path=file_path,
                name=file_path.name,
                stem=file_path.stem,
                ext=file_path.suffix.lstrip(".").lower(),
                size=0,
                mtime_ns=0,
                inode=0,
                device=0,
            )

        if file_path.name in self.unmatched_names:
            decision = Decision(file=fi, kind=DecisionKind.NO_MATCH, reason="no rule matched")
            return PlannedAction(file=fi, status=PlanStatus.NO_MATCH, decision=decision)

        if file_path.name in self.ambiguous_names:
            hit1 = RuleHit(
                rule_name="RuleA",
                priority=100,
                dest_dir=dest_dir,
                static_root=dest_dir,
                matched_by="keyword",
                collision=CollisionPolicy.SKIP,
                create=CreateDestinations.LEAF,
            )
            hit2 = RuleHit(
                rule_name="RuleB",
                priority=100,
                dest_dir=dest_dir,
                static_root=dest_dir,
                matched_by="keyword",
                collision=CollisionPolicy.SKIP,
                create=CreateDestinations.LEAF,
            )
            decision = Decision(
                file=fi,
                kind=DecisionKind.AMBIGUOUS,
                candidates=(hit1, hit2),
            )
            return PlannedAction(file=fi, status=PlanStatus.AMBIGUOUS, decision=decision)

        if file_path.name in self.blocked_names:
            hit = RuleHit(
                rule_name=self.rule_name,
                priority=100,
                dest_dir=dest_dir,
                static_root=dest_dir,
                matched_by="keyword",
                collision=CollisionPolicy.SKIP,
                create=CreateDestinations.LEAF,
            )
            decision = Decision(file=fi, kind=DecisionKind.MATCH, hit=hit)
            return PlannedAction(
                file=fi,
                status=PlanStatus.BLOCKED,
                decision=decision,
                code=ErrorCode.COLLISION,
                message="File collision",
            )

        hit = RuleHit(
            rule_name=self.rule_name,
            priority=100,
            dest_dir=dest_dir,
            static_root=dest_dir,
            matched_by="keyword",
            collision=CollisionPolicy.SKIP,
            create=CreateDestinations.LEAF,
        )
        decision = Decision(file=fi, kind=DecisionKind.MATCH, hit=hit)
        return PlannedAction(
            file=fi,
            status=PlanStatus.WILL_MOVE,
            decision=decision,
            dest_path=dest_dir / file_path.name,
        )

    def apply(
        self,
        plan: Plan,
        *,
        command: str = "organize",
        progress: ProgressCb | None = None,
    ) -> BatchResult:
        self.apply_calls.append(plan)
        results: list[MoveResult] = []
        moved = 0
        skipped = 0
        blocked = 0
        failed = 0

        for a in plan.actions:
            if a.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED):
                if a.dest_path is not None:
                    a.dest_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.move(str(a.file.path), str(a.dest_path))
                        rule_name = a.decision.hit.rule_name if a.decision.hit else self.rule_name
                        results.append(
                            MoveResult(
                                ok=True,
                                src=a.file.path,
                                dst=a.dest_path,
                                strategy=MoveStrategy.RENAME_NOREPLACE,
                                code=ErrorCode.NONE,
                                message=rule_name,
                            )
                        )
                        moved += 1
                    except Exception as e:
                        results.append(
                            MoveResult(
                                ok=False,
                                src=a.file.path,
                                dst=None,
                                strategy=None,
                                code=ErrorCode.UNKNOWN,
                                message=str(e),
                            )
                        )
                        failed += 1
            elif a.status == PlanStatus.BLOCKED:
                blocked += 1
            else:
                skipped += 1

        return BatchResult(
            batch_id=1,
            moved=moved,
            skipped=skipped,
            blocked=blocked,
            failed=failed,
            results=tuple(results),
        )

    def undo(
        self,
        *,
        op_id: int | None = None,
        batch_id: int | None = None,
        dry_run: bool = False,
        on_conflict: str = "skip",
        allow_modified: bool = False,
    ) -> Sequence[UndoOutcome]:
        return ()

    def reload_config(self) -> None:
        self.reload_count += 1
        if self.fail_reload:
            raise RuntimeError("Simulated reload_config failure")
