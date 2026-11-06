"""QAbstractTableModel implementations for ClutterXP GUI.

Provides reactive, typed tables backed by dataclasses from models.py:
- PlanTableModel: PlannedActions with checkable selection column to exclude items.
- RulesTableModel: Configured rules with toggleable enabled state.
- HistoryTableModel: Journal operations and batches.
- DuplicatesTableModel: Duplicate groups, wasted bytes, and file lists.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)

from clutterxp.config.schema import RuleSpec
from clutterxp.models import (
    DuplicateGroup,
    OperationRecord,
    Plan,
    PlannedAction,
    PlanStatus,
)


def format_bytes(bytes_count: int) -> str:
    """Format bytes as human readable KiB/MiB/GiB."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    num = float(bytes_count)
    for unit in ["KiB", "MiB", "GiB", "TiB"]:
        num /= 1024.0
        if num < 1024.0:
            return f"{num:.1f} {unit}"
    return f"{num:.1f} PiB"


_EMPTY_INDEX = QModelIndex()


def _action_matches_filter(action: PlannedAction, filter_status: str) -> bool:
    """Check if action matches filter category."""
    if filter_status == "all":
        return True
    if filter_status == "will_move":
        return action.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED)
    if filter_status == "ambiguous":
        return action.status == PlanStatus.AMBIGUOUS
    if filter_status == "blocked":
        return action.status == PlanStatus.BLOCKED
    if filter_status == "unmatched":
        return action.status in (PlanStatus.NO_MATCH, PlanStatus.IGNORED)
    return True


class PlanTableModel(QAbstractTableModel):
    """Table model displaying PlannedActions with include/exclude checkboxes."""

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "Include",
        "Status",
        "File Name",
        "Destination",
        "Rule",
        "Match Reason",
    )

    plan_changed = Signal()

    def __init__(self, plan: Plan | None = None) -> None:
        super().__init__()
        self._raw_actions: list[PlannedAction] = list(plan.actions) if plan is not None else []
        self._folders = plan.folders if plan is not None else ()
        self._filter_status: str = "all"  # "all", "will_move", "ambiguous", "blocked", "unmatched"
        self._included: dict[int, bool] = {}  # id(action) -> bool
        self._visible_indices: list[int] = []
        self._rebuild_visibility()

    def _rebuild_visibility(self) -> None:
        indices: list[int] = []
        for i, a in enumerate(self._raw_actions):
            act_id = id(a)
            if act_id not in self._included:
                # By default, will_move and will_move_renamed are included
                self._included[act_id] = a.status in (
                    PlanStatus.WILL_MOVE,
                    PlanStatus.WILL_MOVE_RENAMED,
                )

            if _action_matches_filter(a, self._filter_status):
                indices.append(i)

        self._visible_indices = indices

    def set_plan(self, plan: Plan) -> None:
        """Replace current plan and reset view."""
        self.beginResetModel()
        self._raw_actions = list(plan.actions)
        self._folders = plan.folders
        self._included.clear()
        self._rebuild_visibility()
        self.endResetModel()
        self.plan_changed.emit()

    def set_filter(self, filter_name: str) -> None:
        """Set filter: 'all', 'will_move', 'ambiguous', 'blocked', 'unmatched'."""
        self.beginResetModel()
        self._filter_status = filter_name.lower()
        self._rebuild_visibility()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._visible_indices)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._visible_indices):
            return None

        raw_idx = self._visible_indices[index.row()]
        action = self._raw_actions[raw_idx]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            is_inc = self._included.get(id(action), True)
            return Qt.CheckState.Checked if is_inc else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return ""
            elif col == 1:
                return action.status.value
            elif col == 2:
                return action.file.name
            elif col == 3:
                return str(action.dest_path) if action.dest_path is not None else ""
            elif col == 4:
                return action.decision.hit.rule_name if action.decision.hit else ""
            elif col == 5:
                if action.decision.hit:
                    return action.decision.hit.matched_by
                if action.decision.candidates:
                    cands = ", ".join(c.rule_name for c in action.decision.candidates)
                    return f"Candidates: {cands}"
                if action.message:
                    return action.message
                return action.decision.reason or action.code.value

        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if index.isValid() and index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            raw_idx = self._visible_indices[index.row()]
            action = self._raw_actions[raw_idx]
            is_checked = (
                value == Qt.CheckState.Checked
                or value is True
                or value == Qt.CheckState.Checked.value
            )
            self._included[id(action)] = is_checked
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.plan_changed.emit()
            return True
        return False

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            base_flags |= Qt.ItemFlag.ItemIsUserCheckable
        return base_flags

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.COLUMNS)
        ):
            return self.COLUMNS[section]
        return None

    def get_action(self, row: int) -> PlannedAction | None:
        """Get the PlannedAction at visible row."""
        if 0 <= row < len(self._visible_indices):
            raw_idx = self._visible_indices[row]
            return self._raw_actions[raw_idx]
        return None

    def toggle_all(self, checked: bool) -> None:
        """Check or uncheck all visible rows."""
        self.beginResetModel()
        for raw_idx in self._visible_indices:
            action = self._raw_actions[raw_idx]
            self._included[id(action)] = checked
        self.endResetModel()
        self.plan_changed.emit()

    def get_included_plan(self) -> Plan:
        """Return a new Plan containing only actions checked for inclusion."""
        included_actions = [
            a
            for a in self._raw_actions
            if self._included.get(id(a), False)
            and a.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED)
        ]
        return Plan(folders=self._folders, actions=tuple(included_actions))

    @property
    def total_actions_count(self) -> int:
        return len(self._raw_actions)

    @property
    def included_count(self) -> int:
        return sum(
            1
            for a in self._raw_actions
            if self._included.get(id(a), False)
            and a.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED)
        )


class RulesTableModel(QAbstractTableModel):
    """Table model displaying configured rules."""

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "Enabled",
        "Priority",
        "Name",
        "Keywords",
        "Extensions",
        "Destination",
    )

    rule_toggled = Signal(str, bool)  # rule_name, new_enabled

    def __init__(self, rules: Sequence[RuleSpec] | None = None) -> None:
        super().__init__()
        self._rules: list[RuleSpec] = list(rules) if rules is not None else []

    def set_rules(self, rules: Sequence[RuleSpec]) -> None:
        self.beginResetModel()
        self._rules = list(rules)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._rules)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._rules):
            return None

        rule = self._rules[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return ""
            elif col == 1:
                return rule.priority
            elif col == 2:
                return rule.name
            elif col == 3:
                return ", ".join(rule.keywords)
            elif col == 4:
                return ", ".join(rule.extensions)
            elif col == 5:
                return rule.destination

        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if index.isValid() and index.column() == 0 and role == Qt.ItemDataRole.CheckStateRole:
            rule = self._rules[index.row()]
            new_val = (
                value == Qt.CheckState.Checked
                or value is True
                or value == Qt.CheckState.Checked.value
            )
            self.rule_toggled.emit(rule.name, new_val)
            return True
        return False

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            base_flags |= Qt.ItemFlag.ItemIsUserCheckable
        return base_flags

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.COLUMNS)
        ):
            return self.COLUMNS[section]
        return None

    def get_rule(self, row: int) -> RuleSpec | None:
        if 0 <= row < len(self._rules):
            return self._rules[row]
        return None


class HistoryTableModel(QAbstractTableModel):
    """Table model displaying journal operations."""

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "ID",
        "Batch",
        "Status",
        "Source",
        "Destination",
        "Rule",
        "Time",
        "Details",
    )

    def __init__(self, records: Sequence[OperationRecord] | None = None) -> None:
        super().__init__()
        self._records: list[OperationRecord] = list(records) if records is not None else []

    def set_records(self, records: Sequence[OperationRecord]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._records)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._records):
            return None

        rec = self._records[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return rec.id
            elif col == 1:
                return rec.batch_id
            elif col == 2:
                return rec.status.value
            elif col == 3:
                return str(rec.src_path)
            elif col == 4:
                return str(rec.dst_path) if rec.dst_path is not None else ""
            elif col == 5:
                return rec.rule_name or ""
            elif col == 6:
                return rec.created_at
            elif col == 7:
                return rec.error or ""

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.COLUMNS)
        ):
            return self.COLUMNS[section]
        return None

    def get_record(self, row: int) -> OperationRecord | None:
        if 0 <= row < len(self._records):
            return self._records[row]
        return None


class DuplicatesTableModel(QAbstractTableModel):
    """Table model displaying duplicate groups."""

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "Kind",
        "Files Count",
        "Wasted Space",
        "Hash / Note",
        "Files",
    )

    def __init__(self, groups: Sequence[DuplicateGroup] | None = None) -> None:
        super().__init__()
        self._raw_groups: list[DuplicateGroup] = list(groups) if groups is not None else []
        self._filter_kind: str = "all"
        self._visible_groups: list[DuplicateGroup] = []
        self._rebuild_visibility()

    def _rebuild_visibility(self) -> None:
        if self._filter_kind == "all":
            self._visible_groups = list(self._raw_groups)
        else:
            self._visible_groups = [
                g for g in self._raw_groups if g.kind.lower() == self._filter_kind
            ]

    def set_groups(self, groups: Sequence[DuplicateGroup]) -> None:
        self.beginResetModel()
        self._raw_groups = list(groups)
        self._rebuild_visibility()
        self.endResetModel()

    def set_filter(self, kind: str) -> None:
        """Filter by group kind: 'all', 'exact', 'potential', 'hardlink'."""
        self.beginResetModel()
        self._filter_kind = kind.lower()
        self._rebuild_visibility()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._visible_groups)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._visible_groups):
            return None

        group = self._visible_groups[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return group.kind
            elif col == 1:
                return len(group.files)
            elif col == 2:
                return format_bytes(group.wasted_bytes)
            elif col == 3:
                return group.sha256[:16] + "..." if group.sha256 else group.note
            elif col == 4:
                return ", ".join(f.name for f in group.files[:3]) + (
                    f" (+{len(group.files) - 3} more)" if len(group.files) > 3 else ""
                )

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self.COLUMNS)
        ):
            return self.COLUMNS[section]
        return None

    def get_group(self, row: int) -> DuplicateGroup | None:
        if 0 <= row < len(self._visible_groups):
            return self._visible_groups[row]
        return None


__all__ = [
    "DuplicatesTableModel",
    "HistoryTableModel",
    "PlanTableModel",
    "RulesTableModel",
    "format_bytes",
]
