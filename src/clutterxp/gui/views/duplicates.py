"""Duplicates view for multi-stage scanning, progress, cancellation, and quarantine.

Implements acceptance criteria A87 (live progress & clean cancellation) and
A88 (quarantine preview, confirm, moves without deleting, undoable, exact groups only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from clutterxp import paths
from clutterxp.duplicates.quarantine import QuarantinePlan
from clutterxp.gui.api import GuiApi
from clutterxp.gui.models_qt import DuplicatesTableModel, format_bytes
from clutterxp.gui.workers import CancellationToken, run_in_worker
from clutterxp.models import BatchResult, DuplicateGroup, FileInfo
from clutterxp.units import parse_size


class QuarantinePreviewDialog(QDialog):
    """Dialog previewing files to be quarantined and confirming execution."""

    def __init__(
        self,
        plan: QuarantinePlan,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preview Quarantine")
        self.resize(580, 420)
        self.plan = plan

        layout = QVBoxLayout(self)

        info_box = QGroupBox("Quarantine Summary")
        info_layout = QFormLayout(info_box)
        info_layout.addRow("Quarantine Directory:", QLabel(str(plan.quarantine_dir)))
        info_layout.addRow("Keep Strategy:", QLabel(plan.keep_strategy))
        info_layout.addRow("Files to Move:", QLabel(str(plan.total_moves)))
        info_layout.addRow("Wasted Bytes Reclaimed:", QLabel(format_bytes(plan.total_bytes)))
        layout.addWidget(info_box)

        # Kept files preview
        layout.addWidget(QLabel("Files being kept in place (original locations):"))
        kept_list = QListWidget()
        for f in plan.kept_files:
            kept_list.addItem(f"KEEP: {f.path}")
        layout.addWidget(kept_list)

        # Quarantined files preview
        layout.addWidget(QLabel("Files to move into quarantine directory (undoable):"))
        move_list = QListWidget()
        for act in plan.actions:
            move_list.addItem(f"MOVE: {act.file.path} -> {act.target_dir / act.target_name}")
        layout.addWidget(move_list)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Confirm Quarantine")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class DuplicatesView(QWidget):
    """View to detect duplicates and quarantine exact groups."""

    def __init__(self, api: GuiApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._model = DuplicatesTableModel()
        self._current_groups: list[DuplicateGroup] = []
        self._cancel_token = CancellationToken()
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header
        title_label = QLabel("Duplicate File Finder & Quarantine")
        title_font = title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)

        # Folder Picker & Scan Controls Box
        scan_box = QGroupBox("Scan Targets & Parameters")
        scan_layout = QVBoxLayout(scan_box)

        folders_row = QHBoxLayout()
        self.folders_list = QListWidget()
        self.folders_list.setMaximumHeight(80)
        # Default target folder from config if present
        cfg = self.api.get_runtime_config()
        for w in cfg.watch_entries:
            self.folders_list.addItem(w.path)
        folders_row.addWidget(self.folders_list, stretch=1)

        f_btns = QVBoxLayout()
        self.add_folder_btn = QPushButton("Add Folder...")
        self.add_folder_btn.clicked.connect(self._on_add_folder)
        f_btns.addWidget(self.add_folder_btn)

        self.remove_folder_btn = QPushButton("Remove Folder")
        self.remove_folder_btn.clicked.connect(self._on_remove_folder)
        f_btns.addWidget(self.remove_folder_btn)
        f_btns.addStretch()
        folders_row.addLayout(f_btns)

        scan_layout.addLayout(folders_row)

        options_row = QHBoxLayout()
        self.recursive_cb = QCheckBox("Recursive")
        self.recursive_cb.setChecked(True)
        options_row.addWidget(self.recursive_cb)

        self.potential_cb = QCheckBox("Detect Potential Duplicates")
        self.potential_cb.setChecked(True)
        options_row.addWidget(self.potential_cb)

        options_row.addWidget(QLabel("Min Size:"))
        self.min_size_edit = QLineEdit("1B")
        self.min_size_edit.setMaximumWidth(80)
        options_row.addWidget(self.min_size_edit)

        options_row.addStretch()

        self.scan_btn = QPushButton("Start Scan")
        self.scan_btn.setStyleSheet("font-weight: bold; padding: 6px 14px;")
        self.scan_btn.clicked.connect(self.start_scan)
        options_row.addWidget(self.scan_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_scan)
        options_row.addWidget(self.cancel_btn)

        scan_layout.addLayout(options_row)
        main_layout.addWidget(scan_box)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Center area: Filter + Groups Table + Selected Files
        center_splitter = QSplitter(Qt.Orientation.Vertical)

        groups_container = QWidget()
        groups_layout = QVBoxLayout(groups_container)
        groups_layout.setContentsMargins(0, 0, 0, 0)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Show Groups:"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("All Duplicate Groups", "all")
        self.kind_combo.addItem("Exact Duplicates Only", "exact")
        self.kind_combo.addItem("Potential Duplicates", "potential")
        self.kind_combo.addItem("Hardlinks Only", "hardlink")
        self.kind_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.kind_combo)
        filter_row.addStretch()
        groups_layout.addLayout(filter_row)

        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._on_group_selected)
        groups_layout.addWidget(self.table)
        center_splitter.addWidget(groups_container)

        # Details of files in selected group
        details_box = QGroupBox("Files in Selected Duplicate Group")
        details_layout = QVBoxLayout(details_box)

        self.files_list = QListWidget()
        details_layout.addWidget(self.files_list)

        file_btns = QHBoxLayout()
        self.open_fm_btn = QPushButton("Open in File Manager")
        self.open_fm_btn.setEnabled(False)
        self.open_fm_btn.clicked.connect(self._on_open_file_manager)
        file_btns.addWidget(self.open_fm_btn)

        file_btns.addStretch()

        self.quarantine_btn = QPushButton("Quarantine Duplicates...")
        self.quarantine_btn.setStyleSheet("font-weight: bold;")
        self.quarantine_btn.clicked.connect(self._on_quarantine_clicked)
        file_btns.addWidget(self.quarantine_btn)

        details_layout.addLayout(file_btns)
        center_splitter.addWidget(details_box)

        main_layout.addWidget(center_splitter, stretch=1)

    @property
    def model(self) -> DuplicatesTableModel:
        return self._model

    def set_groups(self, groups: list[DuplicateGroup]) -> None:
        """Set groups directly for testing and sync updates."""
        self._current_groups = list(groups)
        self._model.set_groups(groups)
        self._on_group_selected()

    def start_scan(self) -> None:
        """Start duplicate detector scan in background worker."""
        folders = [
            Path(self.folders_list.item(i).text()).expanduser()
            for i in range(self.folders_list.count())
        ]
        if not folders:
            QMessageBox.information(self, "No Folders", "Please add at least one folder to scan.")
            return

        try:
            min_size_bytes = parse_size(self.min_size_edit.text().strip() or "1B")
        except Exception:
            QMessageBox.warning(self, "Invalid Size", "Please specify a valid minimum file size.")
            return

        self._cancel_token.reset()
        self.scan_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)

        def _do_scan(
            *args: Any,
            cancel: Any = None,
            progress: Any = None,
            **kwargs: Any,
        ) -> list[DuplicateGroup]:
            cancel_ev = cancel if cancel is not None else self._cancel_token.event
            return list(
                self.api.duplicates_scan(
                    folders,
                    recursive=self.recursive_cb.isChecked(),
                    min_size=min_size_bytes,
                    potential=self.potential_cb.isChecked(),
                    cancel=cancel_ev,
                    progress=progress,
                )
            )

        def _on_done(groups: list[DuplicateGroup]) -> None:
            self.scan_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.progress_bar.setVisible(False)
            self.set_groups(groups)

        def _on_err(exc: Exception) -> None:
            self.scan_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.progress_bar.setVisible(False)
            if not self._cancel_token.is_cancelled:
                QMessageBox.critical(self, "Duplicate Scan Failed", str(exc))

        run_in_worker(
            _do_scan,
            cancel_token=self._cancel_token,
            with_progress=True,
            on_progress=lambda cur, tot: self.progress_bar.setValue(int(cur * 100 / max(tot, 1))),
            on_result=_on_done,
            on_error=_on_err,
        )

    def cancel_scan(self) -> None:
        """Cancel ongoing duplicate scan."""
        self._cancel_token.cancel()
        self.cancel_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)
        self.progress_bar.setVisible(False)

    def _on_add_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Directory to Scan")
        if chosen:
            self.folders_list.addItem(chosen)

    def _on_remove_folder(self) -> None:
        cur_row = self.folders_list.currentRow()
        if cur_row >= 0:
            self.folders_list.takeItem(cur_row)

    def _on_filter_changed(self) -> None:
        self._model.set_filter(self.kind_combo.currentData())
        self._on_group_selected()

    def _on_group_selected(self) -> None:
        self.files_list.clear()
        selected_indexes = self.table.selectionModel().selectedRows()
        if not selected_indexes:
            self.open_fm_btn.setEnabled(False)
            return

        group = self._model.get_group(selected_indexes[0].row())
        if group is None:
            self.open_fm_btn.setEnabled(False)
            return

        self.open_fm_btn.setEnabled(True)
        for f in group.files:
            item = QListWidgetItem(f"{f.path} ({format_bytes(f.size)})")
            item.setData(Qt.ItemDataRole.UserRole, f)
            self.files_list.addItem(item)

    def _on_open_file_manager(self) -> None:
        selected_items = self.files_list.selectedItems()
        target_path: Path | None = None
        if selected_items:
            f_info: FileInfo = selected_items[0].data(Qt.ItemDataRole.UserRole)
            target_path = f_info.path
        else:
            selected_indexes = self.table.selectionModel().selectedRows()
            if selected_indexes:
                group = self._model.get_group(selected_indexes[0].row())
                if group and group.files:
                    target_path = group.files[0].path

        if target_path and target_path.exists():
            folder_url = QUrl.fromLocalFile(str(target_path.parent))
            QDesktopServices.openUrl(folder_url)

    def _on_quarantine_clicked(self) -> None:
        # Acceptance A88: Quarantine operates strictly on EXACT duplicate groups
        exact_groups = [g for g in self._current_groups if g.kind == "exact" and len(g.files) >= 2]
        if not exact_groups:
            QMessageBox.warning(
                self,
                "Quarantine Not Available",
                "Quarantine requires at least one exact duplicate group with 2 or more files.\n"
                "Potential duplicates and hardlinks cannot be quarantined.",
            )
            return

        quarantine_dir = paths.state_dir() / "quarantine"
        plan = self.api.duplicates_plan_quarantine(exact_groups, quarantine_dir=quarantine_dir)

        if plan.total_moves == 0:
            QMessageBox.information(
                self, "Quarantine", "No files eligible for quarantine in detected groups."
            )
            return

        dialog = QuarantinePreviewDialog(plan, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:

            def _do_quarantine() -> BatchResult:
                return self.api.duplicates_quarantine(plan)

            def _on_done(res: BatchResult) -> None:
                QMessageBox.information(
                    self,
                    "Quarantine Completed",
                    f"Successfully quarantined {res.moved} duplicate file(s) into:\n"
                    f"{plan.quarantine_dir}\n\n"
                    f"Batch #{res.batch_id} recorded in journal.\n"
                    "This operation can be completely undone from the History view.",
                )
                # Re-run scan to update list
                self.start_scan()

            def _on_err(exc: Exception) -> None:
                QMessageBox.critical(self, "Quarantine Failed", str(exc))

            run_in_worker(_do_quarantine, on_result=_on_done, on_error=_on_err)


__all__ = ["DuplicatesView", "QuarantinePreviewDialog"]
