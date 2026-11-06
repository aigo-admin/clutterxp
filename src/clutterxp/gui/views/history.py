"""History view for querying journal operations and performing undo.

Supports filtering by batch, status, rule, or path, and undoing batches or files
while displaying exact refusal reasons verbatim on failure (acceptance A86).
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clutterxp.gui.api import GuiApi
from clutterxp.gui.models_qt import HistoryTableModel
from clutterxp.gui.workers import run_in_worker
from clutterxp.models import OperationRecord, OpStatus, UndoOutcome


class HistoryView(QWidget):
    """Journal history viewer and undo executor."""

    def __init__(self, api: GuiApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._model = HistoryTableModel()
        self.last_refusal_reasons: list[str] = []
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Title
        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)
        title_label = QLabel("Operations History & Journal")
        title_font = title_label.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #f8fafc;")
        header_layout.addWidget(title_label)

        subtitle_label = QLabel(
            "Every move is tracked in a local journal. Restore individual files or entire batches."
        )
        subtitle_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        header_layout.addWidget(subtitle_label)
        main_layout.addLayout(header_layout)

        # Filter Bar
        filter_box = QGroupBox("Filter Operations")
        filter_layout = QHBoxLayout(filter_box)
        filter_layout.setSpacing(8)

        lbl_batch = QLabel("Batch ID:")
        lbl_batch.setStyleSheet("color: #94a3b8;")
        filter_layout.addWidget(lbl_batch)
        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(0, 1_000_000)
        self.batch_spin.setSpecialValueText("All")
        filter_layout.addWidget(self.batch_spin)

        lbl_status = QLabel("Status:")
        lbl_status.setStyleSheet("color: #94a3b8;")
        filter_layout.addWidget(lbl_status)
        self.status_combo = QComboBox()
        self.status_combo.addItem("All", None)
        self.status_combo.addItem("done", OpStatus.DONE)
        self.status_combo.addItem("undone", OpStatus.UNDONE)
        self.status_combo.addItem("failed", OpStatus.FAILED)
        self.status_combo.addItem("skipped", OpStatus.SKIPPED)
        self.status_combo.addItem("pending", OpStatus.PENDING)
        filter_layout.addWidget(self.status_combo)

        lbl_rule = QLabel("Rule:")
        lbl_rule.setStyleSheet("color: #94a3b8;")
        filter_layout.addWidget(lbl_rule)
        self.rule_filter_edit = QLineEdit()
        self.rule_filter_edit.setPlaceholderText("Filter rule name...")
        filter_layout.addWidget(self.rule_filter_edit)

        lbl_path = QLabel("Path:")
        lbl_path.setStyleSheet("color: #94a3b8;")
        filter_layout.addWidget(lbl_path)
        self.path_filter_edit = QLineEdit()
        self.path_filter_edit.setPlaceholderText("Search file path...")
        filter_layout.addWidget(self.path_filter_edit)

        self.filter_btn = QPushButton("Filter")
        self.filter_btn.setProperty("primary", True)
        self.filter_btn.clicked.connect(self.refresh)
        filter_layout.addWidget(self.filter_btn)

        main_layout.addWidget(filter_box)

        # Operations Table
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        main_layout.addWidget(self.table, stretch=1)

        # Selected Record Detail / Refusal Notice Area
        self.detail_group = QGroupBox("Selected Operation Details")
        detail_layout = QVBoxLayout(self.detail_group)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(90)
        detail_layout.addWidget(self.detail_text)

        self.refusal_banner = QLabel()
        self.refusal_banner.setStyleSheet("color: #b30000; font-weight: bold;")
        self.refusal_banner.setWordWrap(True)
        self.refusal_banner.setVisible(False)
        detail_layout.addWidget(self.refusal_banner)

        main_layout.addWidget(self.detail_group)

        # Action Buttons Row
        btn_row = QHBoxLayout()
        self.undo_file_btn = QPushButton("Undo Selected File")
        self.undo_file_btn.setEnabled(False)
        self.undo_file_btn.clicked.connect(self._on_undo_file)
        btn_row.addWidget(self.undo_file_btn)

        self.undo_batch_btn = QPushButton("Undo Entire Batch")
        self.undo_batch_btn.setEnabled(False)
        self.undo_batch_btn.clicked.connect(self._on_undo_batch)
        btn_row.addWidget(self.undo_batch_btn)

        btn_row.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self.refresh_btn)

        main_layout.addLayout(btn_row)

    @property
    def model(self) -> HistoryTableModel:
        return self._model

    def refresh(self) -> None:
        """Fetch filtered journal records in background worker."""
        batch_val = self.batch_spin.value()
        batch_id = batch_val if batch_val > 0 else None
        status = self.status_combo.currentData()
        rule_name = self.rule_filter_edit.text().strip() or None
        path_contains = self.path_filter_edit.text().strip() or None

        def _fetch() -> list[OperationRecord]:
            return list(
                self.api.history_list(
                    batch_id=batch_id,
                    status=status,
                    limit=200,
                    rule_name=rule_name,
                    path_contains=path_contains,
                )
            )

        def _apply(records: list[OperationRecord]) -> None:
            self._model.set_records(records)
            self._on_selection_changed()

        run_in_worker(_fetch, on_result=_apply)

    def _on_selection_changed(self) -> None:
        selected_indexes = self.table.selectionModel().selectedRows()
        if not selected_indexes:
            self.undo_file_btn.setEnabled(False)
            self.undo_batch_btn.setEnabled(False)
            self.detail_text.clear()
            return

        rec = self._model.get_record(selected_indexes[0].row())
        if rec is None:
            return

        can_undo = rec.status == OpStatus.DONE
        self.undo_file_btn.setEnabled(can_undo)
        self.undo_batch_btn.setEnabled(can_undo)

        details = (
            f"Operation #{rec.id} (Batch #{rec.batch_id}) - Status: {rec.status.value}\n"
            f"Source: {rec.src_path}\n"
            f"Destination: {rec.dst_path}\n"
            f"Rule: {rec.rule_name or '(none)'} | "
            f"Strategy: {rec.strategy} | Size: {rec.size} bytes\n"
            f"Timestamp: {rec.created_at}"
        )
        if rec.error:
            details += f"\nError: {rec.error}"
        self.detail_text.setText(details)

    def _handle_undo_outcomes(self, outcomes: Sequence[UndoOutcome], target_desc: str) -> None:
        """Process undo results and display exact refusal reasons verbatim."""
        self.last_refusal_reasons = []
        if not outcomes:
            QMessageBox.information(self, "Undo", f"No operations found to undo for {target_desc}.")
            return

        succeeded = [o for o in outcomes if o.ok]
        failed = [o for o in outcomes if not o.ok]

        if failed:
            for o in failed:
                reason = o.message if o.message else o.code.value
                self.last_refusal_reasons.append(reason)

            refusal_text = "\n".join(f"- {r}" for r in self.last_refusal_reasons)
            self.refusal_banner.setText(f"Undo Refusal Reasons:\n{refusal_text}")
            self.refusal_banner.setVisible(True)

            QMessageBox.warning(
                self,
                "Undo Refused",
                f"Undo could not be completed for {len(failed)} file(s).\n\n"
                f"Exact refusal reasons verbatim:\n{refusal_text}",
            )
        else:
            self.refusal_banner.setVisible(False)
            QMessageBox.information(
                self,
                "Undo Successful",
                f"Successfully restored {len(succeeded)} file(s).",
            )

        self.refresh()

    def _on_undo_file(self) -> None:
        selected_indexes = self.table.selectionModel().selectedRows()
        if not selected_indexes:
            return
        rec = self._model.get_record(selected_indexes[0].row())
        if rec is None:
            return

        reply = QMessageBox.question(
            self,
            "Confirm File Undo",
            f"Restore file '{rec.src_path.name}' to '{rec.src_path}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:

            def _do_undo() -> Sequence[UndoOutcome]:
                return self.api.undo(op_id=rec.id)

            run_in_worker(
                _do_undo,
                on_result=lambda res: self._handle_undo_outcomes(res, f"file #{rec.id}"),
            )

    def _on_undo_batch(self) -> None:
        selected_indexes = self.table.selectionModel().selectedRows()
        if not selected_indexes:
            return
        rec = self._model.get_record(selected_indexes[0].row())
        if rec is None:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Batch Undo",
            f"Undo all moves performed in Batch #{rec.batch_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:

            def _do_undo() -> Sequence[UndoOutcome]:
                return self.api.undo(batch_id=rec.batch_id)

            run_in_worker(
                _do_undo,
                on_result=lambda res: self._handle_undo_outcomes(res, f"batch #{rec.batch_id}"),
            )


__all__ = ["HistoryView"]
