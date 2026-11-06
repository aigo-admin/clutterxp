"""Folders view for managing watched directories and watcher daemon.

Allows adding, removing, and editing watched directories and their settings
(recursive, settle_seconds, ignore, rules), and controlling the watcher daemon.
"""

from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from clutterxp.config.schema import WatchEntry
from clutterxp.gui.api import GuiApi
from clutterxp.gui.workers import run_in_worker
from clutterxp.models import ConfigError

_EMPTY_INDEX = QModelIndex()


class WatchFoldersModel(QAbstractTableModel):
    """Table model displaying watch folder configurations."""

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "Path",
        "Recursive",
        "Settle (s)",
        "Ignore",
        "Rules",
    )

    def __init__(self, entries: list[WatchEntry] | None = None) -> None:
        super().__init__()
        self._entries: list[WatchEntry] = entries or []

    def set_entries(self, entries: list[WatchEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._entries):
            return None

        entry = self._entries[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return entry.path
            elif col == 1:
                return "Yes" if entry.recursive else "No"
            elif col == 2:
                return f"{entry.settle_seconds}s" if entry.settle_seconds is not None else "Default"
            elif col == 3:
                return ", ".join(entry.ignore) if entry.ignore else "(none)"
            elif col == 4:
                return entry.rules if isinstance(entry.rules, str) else ", ".join(entry.rules)

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

    def get_entry(self, row: int) -> WatchEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None


class FolderSettingsDialog(QDialog):
    """Dialog to edit per-folder watcher settings."""

    def __init__(self, entry: WatchEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Settings: {entry.path}")
        self.resize(450, 260)
        self.entry = entry

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.path_label = QLabel(entry.path)
        form.addRow("Folder Path:", self.path_label)

        self.recursive_cb = QCheckBox("Scan subdirectories recursively")
        self.recursive_cb.setChecked(entry.recursive)
        form.addRow("Recursive:", self.recursive_cb)

        self.settle_spin = QDoubleSpinBox()
        self.settle_spin.setRange(0.0, 3600.0)
        self.settle_spin.setSingleStep(0.5)
        self.settle_spin.setValue(entry.settle_seconds if entry.settle_seconds is not None else 3.0)
        form.addRow("Settle Seconds:", self.settle_spin)

        self.ignore_edit = QLineEdit(", ".join(entry.ignore))
        self.ignore_edit.setPlaceholderText("e.g. *.tmp, .git*, node_modules")
        form.addRow("Ignore Patterns:", self.ignore_edit)

        rules_str = entry.rules if isinstance(entry.rules, str) else ", ".join(entry.rules)
        self.rules_edit = QLineEdit(rules_str)
        self.rules_edit.setPlaceholderText("'all' or comma-separated rule names")
        form.addRow("Active Rules:", self.rules_edit)

        layout.addLayout(form)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_updates(self) -> dict[str, Any]:
        """Extract user changes as an update dictionary."""
        ignore_raw = [s.strip() for s in self.ignore_edit.text().split(",") if s.strip()]
        rules_text = self.rules_edit.text().strip()
        rules_val: str | list[str] = "all"
        if rules_text and rules_text.lower() != "all":
            rules_val = [r.strip() for r in rules_text.split(",") if r.strip()]

        return {
            "recursive": self.recursive_cb.isChecked(),
            "settle_seconds": float(self.settle_spin.value()),
            "ignore": ignore_raw,
            "rules": rules_val,
        }


class FoldersView(QWidget):
    """View to configure watch folders and control the background watcher daemon."""

    def __init__(self, api: GuiApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._model = WatchFoldersModel()
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Header with Title and Watcher Control
        top_bar = QHBoxLayout()
        title_label = QLabel("Watch Folders")
        title_font = title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        top_bar.addWidget(title_label)

        top_bar.addStretch()

        self.watcher_status_label = QLabel("Watcher: Checking...")
        top_bar.addWidget(self.watcher_status_label)

        self.watcher_toggle_btn = QPushButton("Start Watcher")
        self.watcher_toggle_btn.clicked.connect(self._on_toggle_watcher)
        top_bar.addWidget(self.watcher_toggle_btn)

        main_layout.addLayout(top_bar)

        # Table of folders
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        main_layout.addWidget(self.table, stretch=1)

        # Buttons row
        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add Folder...")
        self.add_btn.clicked.connect(self._on_add_folder)
        btn_row.addWidget(self.add_btn)

        self.edit_btn = QPushButton("Edit Settings...")
        self.edit_btn.clicked.connect(self._on_edit_folder)
        btn_row.addWidget(self.edit_btn)

        self.remove_btn = QPushButton("Remove Folder")
        self.remove_btn.clicked.connect(self._on_remove_folder)
        btn_row.addWidget(self.remove_btn)

        btn_row.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self.refresh_btn)

        main_layout.addLayout(btn_row)

    def refresh(self) -> None:
        """Reload configuration and update view."""

        def _fetch() -> tuple[list[WatchEntry], str]:
            cfg = self.api.get_runtime_config(force_reload=True)
            status = self.api.watcher_status()
            return list(cfg.watch_entries), status

        def _apply(res: tuple[list[WatchEntry], str]) -> None:
            entries, status = res
            self._model.set_entries(entries)
            if status == "running":
                self.watcher_status_label.setText("Watcher: RUNNING")
                self.watcher_toggle_btn.setText("Stop Watcher")
            else:
                self.watcher_status_label.setText("Watcher: STOPPED")
                self.watcher_toggle_btn.setText("Start Watcher")

        run_in_worker(_fetch, on_result=_apply)

    def _on_toggle_watcher(self) -> None:
        status = self.api.watcher_status()
        if status == "running":
            self.api.watcher_stop()
        else:
            self.api.watcher_start()
        self.refresh()

    def _on_add_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Folder to Watch")
        if not chosen:
            return

        def _do_add() -> None:
            entry = WatchEntry(path=chosen, recursive=False, settle_seconds=3.0)
            self.api.config_add_watch(entry)

        def _on_done(_: Any) -> None:
            self.refresh()

        def _on_err(exc: Exception) -> None:
            msg = str(exc)
            if isinstance(exc, ConfigError) and exc.problems:
                msg += ":\n\n• " + "\n• ".join(exc.problems)
            QMessageBox.critical(self, "Error Adding Folder", msg)

        run_in_worker(_do_add, on_result=_on_done, on_error=_on_err)

    def _on_edit_folder(self) -> None:
        selected_indexes = self.table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "Selection Required", "Please select a folder to edit.")
            return

        row = selected_indexes[0].row()
        entry = self._model.get_entry(row)
        if entry is None:
            return

        dialog = FolderSettingsDialog(entry, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updates = dialog.get_updates()

            def _do_edit() -> None:
                self.api.config_edit_watch(entry.path, updates)

            def _on_done(_: Any) -> None:
                self.refresh()

            def _on_err(exc: Exception) -> None:
                msg = str(exc)
                if isinstance(exc, ConfigError) and exc.problems:
                    msg += ":\n\n• " + "\n• ".join(exc.problems)
                QMessageBox.critical(self, "Error Updating Settings", msg)

            run_in_worker(_do_edit, on_result=_on_done, on_error=_on_err)

    def _on_remove_folder(self) -> None:
        selected_indexes = self.table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "Selection Required", "Please select a folder to remove.")
            return

        row = selected_indexes[0].row()
        entry = self._model.get_entry(row)
        if entry is None:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Remove watch folder '{entry.path}' from configuration?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:

            def _do_remove() -> None:
                self.api.config_remove_watch(entry.path)

            def _on_done(_: Any) -> None:
                self.refresh()

            def _on_err(exc: Exception) -> None:
                msg = str(exc)
                if isinstance(exc, ConfigError) and exc.problems:
                    msg += ":\n\n• " + "\n• ".join(exc.problems)
                QMessageBox.critical(self, "Error Removing Folder", msg)

            run_in_worker(_do_remove, on_result=_on_done, on_error=_on_err)


__all__ = ["FolderSettingsDialog", "FoldersView", "WatchFoldersModel"]
