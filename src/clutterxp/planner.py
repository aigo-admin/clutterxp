"""Planner module for evaluating rule decisions and building an execution Plan.

Converts FileInfo streams into PlannedAction entries, resolving destinations,
checking permissions and collision policies, and ensuring zero writes to disk.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from clutterxp import safety
from clutterxp.config.runtime import RuntimeConfig
from clutterxp.interfaces import RuleEngine
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    DecisionKind,
    ErrorCode,
    FileInfo,
    Plan,
    PlannedAction,
    PlanStatus,
    SafetyRefusal,
)
from clutterxp.mover.collision import candidate_names


def build_plan(
    infos: Iterable[FileInfo],
    engine: RuleEngine,
    config: RuntimeConfig | None = None,
    *,
    fs_check: bool = True,
    folder: Path | None = None,
    folders: Sequence[Path] | None = None,
) -> Plan:
    """Build a Plan from FileInfo objects using the given RuleEngine.

    - Resolves destination path for matching rules.
    - Safety checks destination against denylist (docs/09 S11).
    - Checks watched folder containment unless allow_chain is True (docs/09 S9).
    - Performs collision checks (skip vs rename) when fs_check is True.
    - Pre-checks destination directory existence per create mode (never, leaf, all).
    - Checks writability of destination directory.
    - Performs ZERO disk writes and ZERO file content reads (Invariants S6, S7).
    """
    info_list = list(infos)
    actions: list[PlannedAction] = []

    if folders is not None:
        plan_folders = tuple(folders)
    elif folder is not None:
        plan_folders = (folder,)
    else:
        plan_folders = tuple(sorted({fi.path.parent for fi in info_list}))

    for info in info_list:
        decision = engine.decide(info, folder=folder)

        if decision.kind == DecisionKind.NO_MATCH:
            actions.append(
                PlannedAction(
                    file=info,
                    status=PlanStatus.NO_MATCH,
                    decision=decision,
                    dest_path=None,
                    code=ErrorCode.NONE,
                    message=decision.reason or "No rule matched",
                )
            )
            continue

        if decision.kind == DecisionKind.AMBIGUOUS:
            cand_names = ", ".join(c.rule_name for c in decision.candidates)
            actions.append(
                PlannedAction(
                    file=info,
                    status=PlanStatus.AMBIGUOUS,
                    decision=decision,
                    dest_path=None,
                    code=ErrorCode.NONE,
                    message=f"Ambiguous match across rules: {cand_names}",
                )
            )
            continue

        if decision.kind == DecisionKind.IGNORED:
            actions.append(
                PlannedAction(
                    file=info,
                    status=PlanStatus.IGNORED,
                    decision=decision,
                    dest_path=None,
                    code=ErrorCode.NONE,
                    message=decision.reason or "Ignored",
                )
            )
            continue

        # DecisionKind.MATCH
        hit = decision.hit
        if hit is None:
            actions.append(
                PlannedAction(
                    file=info,
                    status=PlanStatus.BLOCKED,
                    decision=decision,
                    dest_path=None,
                    code=ErrorCode.UNKNOWN,
                    message="Match decision without rule hit",
                )
            )
            continue

        dest_dir = hit.dest_dir
        dest_path = dest_dir / info.name

        # Safety check destination
        try:
            safety.check_destination(dest_path)
        except SafetyRefusal as e:
            actions.append(
                PlannedAction(
                    file=info,
                    status=PlanStatus.BLOCKED,
                    decision=decision,
                    dest_path=dest_path,
                    code=ErrorCode.DEST_INVALID,
                    message=str(e),
                )
            )
            continue

        # Invariant S9: Destination inside watched folder without allow_chain
        if config is not None and not config.settings.allow_chain:
            dest_resolved = dest_path.resolve()
            is_inside_watch = False
            for w in config.watch_entries:
                try:
                    w_resolved = Path(w.path).resolve()
                    dest_resolved.relative_to(w_resolved)
                    is_inside_watch = True
                    break
                except (ValueError, OSError):
                    pass
            if is_inside_watch:
                actions.append(
                    PlannedAction(
                        file=info,
                        status=PlanStatus.BLOCKED,
                        decision=decision,
                        dest_path=dest_path,
                        code=ErrorCode.DEST_INVALID,
                        message="Destination is inside a watched folder (allow_chain is False)",
                    )
                )
                continue

        # Precondition: Destination directory is not source's own directory (no-op)
        try:
            if dest_path.resolve() == info.path.resolve():
                actions.append(
                    PlannedAction(
                        file=info,
                        status=PlanStatus.BLOCKED,
                        decision=decision,
                        dest_path=dest_path,
                        code=ErrorCode.DEST_INVALID,
                        message="Destination directory is source's own directory (no-op)",
                    )
                )
                continue
        except OSError:
            pass

        if not fs_check:
            actions.append(
                PlannedAction(
                    file=info,
                    status=PlanStatus.WILL_MOVE,
                    decision=decision,
                    dest_path=dest_path,
                    code=ErrorCode.NONE,
                    message="",
                )
            )
            continue

        # Check CreateDestinations policy
        create_policy = hit.create
        if create_policy == CreateDestinations.NEVER:
            if not dest_dir.exists() or not dest_dir.is_dir():
                actions.append(
                    PlannedAction(
                        file=info,
                        status=PlanStatus.BLOCKED,
                        decision=decision,
                        dest_path=dest_path,
                        code=ErrorCode.DEST_MISSING,
                        message=(
                            f"Destination directory '{dest_dir}' does not exist (create: never)"
                        ),
                    )
                )
                continue
        elif create_policy == CreateDestinations.LEAF:
            static_root = hit.static_root
            if static_root is not None:
                if not static_root.exists() and not static_root.parent.exists():
                    actions.append(
                        PlannedAction(
                            file=info,
                            status=PlanStatus.BLOCKED,
                            decision=decision,
                            dest_path=dest_path,
                            code=ErrorCode.DEST_MISSING,
                            message=(
                                f"Static root parent '{static_root.parent}' does not exist "
                                "(create: leaf)"
                            ),
                        )
                    )
                    continue
                # If dest_dir is not relative to static_root, ensure its parent exists
                try:
                    dest_dir.relative_to(static_root)
                except ValueError:
                    if not dest_dir.exists() and not dest_dir.parent.exists():
                        actions.append(
                            PlannedAction(
                                file=info,
                                status=PlanStatus.BLOCKED,
                                decision=decision,
                                dest_path=dest_path,
                                code=ErrorCode.DEST_MISSING,
                                message=(
                                    f"Destination parent '{dest_dir.parent}' does not exist "
                                    "(create: leaf)"
                                ),
                            )
                        )
                        continue
            else:
                if not dest_dir.exists() and not dest_dir.parent.exists():
                    actions.append(
                        PlannedAction(
                            file=info,
                            status=PlanStatus.BLOCKED,
                            decision=decision,
                            dest_path=dest_path,
                            code=ErrorCode.DEST_MISSING,
                            message=(
                                f"Destination parent '{dest_dir.parent}' does not exist "
                                "(create: leaf)"
                            ),
                        )
                    )
                    continue

        # Check writability of existing ancestor
        check_dir = dest_dir
        while not check_dir.exists() and check_dir != check_dir.parent:
            check_dir = check_dir.parent
        if check_dir.exists() and not os.access(check_dir, os.W_OK | os.X_OK):
            actions.append(
                PlannedAction(
                    file=info,
                    status=PlanStatus.BLOCKED,
                    decision=decision,
                    dest_path=dest_path,
                    code=ErrorCode.DEST_NOT_WRITABLE,
                    message=f"Destination directory '{check_dir}' is not writable",
                )
            )
            continue

        # Collision pre-check
        if dest_path.exists():
            if hit.collision == CollisionPolicy.SKIP:
                actions.append(
                    PlannedAction(
                        file=info,
                        status=PlanStatus.BLOCKED,
                        decision=decision,
                        dest_path=dest_path,
                        code=ErrorCode.COLLISION,
                        message=f"Destination path '{dest_path}' already exists (collision: skip)",
                    )
                )
                continue
            elif hit.collision == CollisionPolicy.RENAME:
                renamed_dest: Path | None = None
                for cand_name in candidate_names(info.name):
                    cand_path = dest_dir / cand_name
                    if not cand_path.exists():
                        renamed_dest = cand_path
                        break
                if renamed_dest is not None:
                    actions.append(
                        PlannedAction(
                            file=info,
                            status=PlanStatus.WILL_MOVE_RENAMED,
                            decision=decision,
                            dest_path=renamed_dest,
                            code=ErrorCode.NONE,
                            message=f"Collision detected; will rename to '{renamed_dest.name}'",
                        )
                    )
                    continue
                else:
                    actions.append(
                        PlannedAction(
                            file=info,
                            status=PlanStatus.BLOCKED,
                            decision=decision,
                            dest_path=dest_path,
                            code=ErrorCode.COLLISION,
                            message="All collision rename candidates exhausted",
                        )
                    )
                    continue

        actions.append(
            PlannedAction(
                file=info,
                status=PlanStatus.WILL_MOVE,
                decision=decision,
                dest_path=dest_path,
                code=ErrorCode.NONE,
                message="",
            )
        )

    return Plan(folders=plan_folders, actions=tuple(actions))


__all__ = ["build_plan"]
