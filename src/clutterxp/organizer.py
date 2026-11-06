"""Organizer facade coordinating Scanner, RuleEngine, Planner, Mover, and History.

Implements Organizer Protocol from clutterxp.interfaces.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from clutterxp import paths, safety
from clutterxp.config import loader
from clutterxp.config.runtime import RuntimeConfig
from clutterxp.interfaces import History, Mover, Organizer, RuleEngine
from clutterxp.locking import fs_lock
from clutterxp.models import (
    BatchResult,
    ErrorCode,
    FileInfo,
    MoveResult,
    Plan,
    PlannedAction,
    PlanStatus,
    ProgressCb,
    UndoOutcome,
)
from clutterxp.planner import build_plan
from clutterxp.rules import build_engine
from clutterxp.scanner import scan

logger = logging.getLogger(__name__)


def needs_confirmation(plan: Plan, threshold: int = 10) -> bool:
    """Return True if plan moves at least threshold files (or if threshold == 0 and files > 0)."""
    moves = plan.count(PlanStatus.WILL_MOVE) + plan.count(PlanStatus.WILL_MOVE_RENAMED)
    if moves == 0:
        return False
    if threshold == 0:
        return True
    return moves >= threshold


class OrganizerImpl(Organizer):
    """Facade implementation wires together RuleEngine, Mover, History and Planner."""

    def __init__(
        self,
        config: RuntimeConfig,
        engine: RuleEngine,
        mover: Mover,
        history: History,
        *,
        undo_service: Any = None,
        fs_lock_path: Path | None = None,
    ) -> None:
        self.config: RuntimeConfig = config
        self.engine: RuleEngine = engine
        self.mover: Mover = mover
        self.history: History = history
        self.undo_service: Any = undo_service
        self._fs_lock_path: Path | None = fs_lock_path

    @property
    def fs_lock_path(self) -> Path:
        """Filesystem lock path."""
        if self._fs_lock_path is not None:
            return self._fs_lock_path
        return paths.fs_lock_path()

    def plan(
        self,
        paths: Iterable[Path] | None = None,
        *,
        recursive: bool | None = None,
        only_rules: Sequence[str] | None = None,
    ) -> Plan:
        """Scan specified folders (or watch_entries) and build a dry-run execution Plan."""
        all_actions: list[PlannedAction] = []
        scanned_folders: list[Path] = []

        if only_rules is not None:
            target_names = {r.casefold() for r in only_rules}
            active_rules = [r for r in self.config.rules if r.name.casefold() in target_names]
            plan_engine: RuleEngine = build_engine(
                active_rules,
                self.config.settings,
                watch=list(self.config.watch_entries),
            )
        else:
            plan_engine = self.engine

        if paths is None:
            for entry in self.config.watch_entries:
                folder_p = Path(entry.path)
                rec = entry.recursive if recursive is None else recursive
                safety.check_source_root(folder_p, recursive=rec)
                scanned_folders.append(folder_p)
                infos = scan(
                    folder_p,
                    recursive=rec,
                    settings=self.config.settings,
                    ignore_patterns=entry.ignore,
                )
                folder_plan = build_plan(
                    infos,
                    plan_engine,
                    self.config,
                    fs_check=True,
                    folder=folder_p,
                )
                all_actions.extend(folder_plan.actions)
        else:
            for p in paths:
                folder_p = Path(p)
                if folder_p.is_file():
                    safety.check_source_root(folder_p.parent, recursive=False)
                    scanned_folders.append(folder_p.parent)
                    infos_iter = iter([FileInfo.from_path(folder_p)])
                    folder_plan = build_plan(
                        infos_iter,
                        plan_engine,
                        self.config,
                        fs_check=True,
                        folder=folder_p.parent,
                    )
                    all_actions.extend(folder_plan.actions)
                else:
                    rec = False if recursive is None else recursive
                    safety.check_source_root(folder_p, recursive=rec)
                    scanned_folders.append(folder_p)
                    infos = scan(
                        folder_p,
                        recursive=rec,
                        settings=self.config.settings,
                        ignore_patterns=(),
                    )
                    folder_plan = build_plan(
                        infos,
                        plan_engine,
                        self.config,
                        fs_check=True,
                        folder=folder_p,
                    )
                    all_actions.extend(folder_plan.actions)

        return Plan(folders=tuple(scanned_folders), actions=tuple(all_actions))

    def apply(
        self,
        plan: Plan,
        *,
        command: str = "organize",
        progress: ProgressCb | None = None,
    ) -> BatchResult:
        """Execute plan actions under journal-first protocol and fs lock."""
        first_folder = plan.folders[0] if plan.folders else None
        batch_id = self.history.begin_batch(command, folder=first_folder)

        moved = 0
        skipped = 0
        blocked = 0
        failed = 0
        results: list[MoveResult] = []

        for action in plan.actions:
            if action.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED):
                if action.decision.hit is None or action.dest_path is None:
                    failed += 1
                    res = MoveResult(
                        ok=False,
                        src=action.file.path,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.UNKNOWN,
                        message="Missing rule hit or destination path for planned move",
                    )
                    results.append(res)
                    continue

                with fs_lock(self.fs_lock_path):
                    # TOCTOU check: re-verify source file exists and is unchanged
                    try:
                        st = action.file.path.lstat()
                    except FileNotFoundError:
                        failed += 1
                        res = MoveResult(
                            ok=False,
                            src=action.file.path,
                            dst=None,
                            strategy=None,
                            code=ErrorCode.SRC_VANISHED,
                            message=f"Source file {action.file.path} vanished since plan",
                        )
                        results.append(res)
                        self.history.record_event(
                            "error",
                            res.message,
                            batch_id=batch_id,
                            path=action.file.path,
                        )
                        continue
                    except OSError as e:
                        failed += 1
                        res = MoveResult(
                            ok=False,
                            src=action.file.path,
                            dst=None,
                            strategy=None,
                            code=ErrorCode.PERMISSION,
                            message=str(e),
                        )
                        results.append(res)
                        self.history.record_event(
                            "error",
                            res.message,
                            batch_id=batch_id,
                            path=action.file.path,
                        )
                        continue

                    if st.st_size != action.file.size or st.st_mtime_ns != action.file.mtime_ns:
                        failed += 1
                        res = MoveResult(
                            ok=False,
                            src=action.file.path,
                            dst=None,
                            strategy=None,
                            code=ErrorCode.SRC_CHANGED,
                            message=f"Source file {action.file.path} changed since plan",
                        )
                        results.append(res)
                        self.history.record_event(
                            "error",
                            res.message,
                            batch_id=batch_id,
                            path=action.file.path,
                        )
                        continue

                    # Invariant S5: Journal durable pending row before move
                    op_id = self.history.begin_op(
                        batch_id,
                        action.file.path,
                        action.decision.hit.rule_name,
                        action.file,
                    )

                    result = self.mover.move_file(
                        action.file.path,
                        action.dest_path.parent,
                        new_name=action.dest_path.name,
                        collision=action.decision.hit.collision,
                        create=action.decision.hit.create,
                        static_root=action.decision.hit.static_root,
                        expect_size=action.file.size,
                        expect_mtime_ns=action.file.mtime_ns,
                        progress=progress,
                    )

                    self.history.finish_op(op_id, result)
                    results.append(result)

                    if result.ok:
                        moved += 1
                    else:
                        if result.code == ErrorCode.COLLISION:
                            blocked += 1
                        else:
                            failed += 1

            elif action.status == PlanStatus.AMBIGUOUS:
                skipped += 1
                self.history.record_event(
                    "skip_ambiguous",
                    f"File '{action.file.path}' is ambiguous: {action.message}",
                    batch_id=batch_id,
                    path=action.file.path,
                )
            elif action.status == PlanStatus.BLOCKED:
                blocked += 1
                self.history.record_event(
                    "skip_blocked",
                    f"File '{action.file.path}' is blocked ({action.code.value}): {action.message}",
                    batch_id=batch_id,
                    path=action.file.path,
                )
            elif action.status == PlanStatus.NO_MATCH:
                skipped += 1
                if self.config.settings.history.log_no_match:
                    self.history.record_event(
                        "skip_no_match",
                        f"File '{action.file.path}' matched no rules",
                        batch_id=batch_id,
                        path=action.file.path,
                    )
            elif action.status == PlanStatus.IGNORED:
                skipped += 1

        return BatchResult(
            batch_id=batch_id,
            moved=moved,
            skipped=skipped,
            blocked=blocked,
            failed=failed,
            results=tuple(results),
        )

    def process_file(
        self, path: Path, *, folder: Path | None = None, command: str = "watch"
    ) -> BatchResult:
        """Single-file processing used by watcher: decide -> plan -> apply as a batch of 1."""
        p = Path(path)
        if not p.exists():
            return BatchResult(
                batch_id=None,
                moved=0,
                skipped=0,
                blocked=0,
                failed=1,
                results=(
                    MoveResult(
                        ok=False,
                        src=p,
                        dst=None,
                        strategy=None,
                        code=ErrorCode.SRC_VANISHED,
                        message=f"File {p} does not exist",
                    ),
                ),
            )

        info = FileInfo.from_path(p)
        plan_folder = folder if folder is not None else p.parent
        plan = build_plan([info], self.engine, self.config, fs_check=True, folder=plan_folder)
        return self.apply(plan, command=command)

    def undo(
        self,
        *,
        op_id: int | None = None,
        batch_id: int | None = None,
        dry_run: bool = False,
        on_conflict: str = "skip",
        allow_modified: bool = False,
    ) -> Sequence[UndoOutcome]:
        """Delegate undo to the injected UndoService."""
        if self.undo_service is None:
            raise NotImplementedError("UndoService not configured")

        outcome: Sequence[UndoOutcome] = self.undo_service.undo(
            op_id=op_id,
            batch_id=batch_id,
            dry_run=dry_run,
            on_conflict=on_conflict,
            allow_modified=allow_modified,
        )
        return outcome

    def reload_config(self) -> None:
        """Reload configuration from disk and atomically update engine if valid."""
        try:
            new_config = loader.load(self.config.config_path)
            if new_config.is_valid:
                new_engine = build_engine(
                    list(new_config.rules),
                    new_config.settings,
                    watch=list(new_config.watch_entries),
                )
                self.config = new_config
                self.engine = new_engine
                logger.info("Config successfully reloaded from %s", self.config.config_path)
            else:
                logger.error(
                    "Config reload rejected due to validation errors: %s",
                    ", ".join(new_config.errors),
                )
        except Exception as e:
            logger.error("Config reload failed with exception: %s", e)

    def needs_confirmation(self, plan: Plan) -> bool:
        """Check if plan requires confirmation against confirm_threshold."""
        return needs_confirmation(plan, self.config.settings.confirm_threshold)


__all__ = ["OrganizerImpl", "needs_confirmation"]
