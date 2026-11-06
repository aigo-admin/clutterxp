"""Rules view and editor dialog with live filename trace.

Allows managing organization rules (add, edit, remove, enable/disable) and
features a live 'Test Filename' trace evaluator showing real-time rule matching.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from clutterxp.config.placeholders import validate_destination_template
from clutterxp.config.schema import Conditions, RuleSpec
from clutterxp.gui.api import GuiApi
from clutterxp.gui.models_qt import RulesTableModel
from clutterxp.gui.workers import run_in_worker
from clutterxp.models import (
    CollisionPolicy,
    ConfigError,
    CreateDestinations,
    MatchMode,
    RuleTrace,
)

_EMPTY_INDEX = QModelIndex()


class TraceTableModel(QAbstractTableModel):
    """Table model displaying real-time rule trace results."""

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "Rule Name",
        "Matched",
        "Priority",
        "Reasons",
    )

    def __init__(self, traces: Sequence[RuleTrace] | None = None) -> None:
        super().__init__()
        self._traces: list[RuleTrace] = list(traces) if traces is not None else []

    def set_traces(self, traces: Sequence[RuleTrace]) -> None:
        self.beginResetModel()
        self._traces = list(traces)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self._traces)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.row() >= len(self._traces):
            return None

        trace = self._traces[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return trace.rule_name
            elif col == 1:
                return "MATCH" if trace.matched else "NO MATCH"
            elif col == 2:
                return trace.priority
            elif col == 3:
                return "; ".join(trace.reasons)

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


class RuleEditorDialog(QDialog):
    """Dialog to create or edit an organization rule."""

    def __init__(
        self,
        rule: RuleSpec | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.is_edit = rule is not None
        self.original_name = rule.name if rule is not None else ""
        self.setWindowTitle("Edit Rule" if self.is_edit else "Add New Rule")
        self.resize(560, 520)

        self._init_ui(rule)
        self._update_dest_preview()

    def _init_ui(self, rule: RuleSpec | None) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Quick Presets Row for instant, simple setup
        preset_box = QHBoxLayout()
        preset_box.addWidget(QLabel("Quick Presets:"))
        preset_invoices = QPushButton("📄 Invoices")
        preset_invoices.clicked.connect(
            lambda: self._apply_preset(
                "Invoices",
                "invoice, receipt, bill, statement",
                "pdf",
                "~/Documents/Invoices/{year}/{month}",
            )
        )
        preset_box.addWidget(preset_invoices)

        preset_photos = QPushButton("🖼️ Photos")
        preset_photos.clicked.connect(
            lambda: self._apply_preset(
                "Photos", "", "jpg, jpeg, png, heic, raw", "~/Pictures/{year}/{month}"
            )
        )
        preset_box.addWidget(preset_photos)

        preset_installers = QPushButton("📦 Installers")
        preset_installers.clicked.connect(
            lambda: self._apply_preset(
                "Installers", "", "deb, rpm, appimage, tar.gz, iso", "~/Downloads/Installers"
            )
        )
        preset_box.addWidget(preset_installers)
        preset_box.addStretch()
        layout.addLayout(preset_box)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(rule.name if rule else "")
        self.name_edit.setPlaceholderText("e.g. Invoices, Receipts, Photos")
        form.addRow("Rule Name:", self.name_edit)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 100_000)
        self.priority_spin.setValue(rule.priority if rule else 100)
        form.addRow("Priority (lower runs first):", self.priority_spin)

        self.keywords_edit = QLineEdit(", ".join(rule.keywords) if rule else "")
        self.keywords_edit.setPlaceholderText("e.g. invoice, bill, receipt, statement")
        form.addRow("Keywords (comma-separated):", self.keywords_edit)

        self.extensions_edit = QLineEdit(", ".join(rule.extensions) if rule else "")
        self.extensions_edit.setPlaceholderText("e.g. pdf, png, jpg (without dots)")
        form.addRow("Extensions:", self.extensions_edit)

        # Match mode
        self.match_combo = QComboBox()
        self.match_combo.addItem("Default (inherit from settings)", None)
        self.match_combo.addItem("Token (exact word boundary)", MatchMode.TOKEN)
        self.match_combo.addItem("Prefix (word starts with keyword)", MatchMode.PREFIX)
        self.match_combo.addItem("Substring (anywhere in filename)", MatchMode.SUBSTRING)
        if rule and rule.match:
            idx = self.match_combo.findData(rule.match)
            if idx >= 0:
                self.match_combo.setCurrentIndex(idx)
        form.addRow("Match Mode:", self.match_combo)

        # Destination template & helpers
        dest_box = QVBoxLayout()
        self.dest_edit = QLineEdit(rule.destination if rule else "~/Documents/{year}/{month}")
        self.dest_edit.textChanged.connect(self._update_dest_preview)
        dest_box.addWidget(self.dest_edit)

        # Template helper chips/buttons
        chips_layout = QHBoxLayout()
        chips_layout.addWidget(QLabel("Insert placeholder:"))
        for placeholder in ["{year}", "{month}", "{day}", "{ext}", "{rule}"]:
            btn = QPushButton(placeholder)
            btn.setMaximumWidth(65)
            btn.clicked.connect(lambda _, ph=placeholder: self._insert_placeholder(ph))
            chips_layout.addWidget(btn)
        chips_layout.addStretch()
        dest_box.addLayout(chips_layout)

        self.dest_preview_label = QLabel()
        self.dest_preview_label.setStyleSheet("color: #2b7a0b; font-style: italic;")
        dest_box.addWidget(self.dest_preview_label)

        form.addRow("Destination:", dest_box)

        # Conditions
        self.min_size_edit = QLineEdit(
            str(rule.conditions.min_size) if rule and rule.conditions.min_size else ""
        )
        self.min_size_edit.setPlaceholderText("e.g. 10KB, 1MB (optional)")
        form.addRow("Min Size:", self.min_size_edit)

        self.max_size_edit = QLineEdit(
            str(rule.conditions.max_size) if rule and rule.conditions.max_size else ""
        )
        self.max_size_edit.setPlaceholderText("e.g. 50MB (optional)")
        form.addRow("Max Size:", self.max_size_edit)

        # Collision & Destination creation
        self.collision_combo = QComboBox()
        self.collision_combo.addItem("Default (inherit from settings)", None)
        self.collision_combo.addItem("Skip (leave in place)", CollisionPolicy.SKIP)
        self.collision_combo.addItem("Rename (add ' (1)', ' (2)')", CollisionPolicy.RENAME)
        if rule and rule.collision:
            idx = self.collision_combo.findData(rule.collision)
            if idx >= 0:
                self.collision_combo.setCurrentIndex(idx)
        form.addRow("Collision Policy:", self.collision_combo)

        self.create_combo = QComboBox()
        self.create_combo.addItem("Default (inherit from settings)", None)
        self.create_combo.addItem("Leaf (create leaf directory only)", CreateDestinations.LEAF)
        self.create_combo.addItem("All (create all parent directories)", CreateDestinations.ALL)
        self.create_combo.addItem("Never (destination must exist)", CreateDestinations.NEVER)
        if rule and rule.create_destinations:
            idx = self.create_combo.findData(rule.create_destinations)
            if idx >= 0:
                self.create_combo.setCurrentIndex(idx)
        form.addRow("Create Destination:", self.create_combo)

        layout.addLayout(form)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _apply_preset(self, name: str, keywords: str, extensions: str, dest: str) -> None:
        """Pre-fill fields from a selected preset."""
        self.name_edit.setText(name)
        self.keywords_edit.setText(keywords)
        self.extensions_edit.setText(extensions)
        self.dest_edit.setText(dest)
        self._update_dest_preview()

    def _insert_placeholder(self, placeholder: str) -> None:
        self.dest_edit.insert(placeholder)

    def _update_dest_preview(self) -> None:
        dest_text = self.dest_edit.text()
        now = datetime.now(timezone.utc)
        sample = dest_text.replace("{year}", str(now.year))
        sample = sample.replace("{month}", f"{now.month:02d}")
        sample = sample.replace("{day}", f"{now.day:02d}")
        sample = sample.replace("{ext}", "pdf")
        sample = sample.replace("{rule}", self.name_edit.text().strip() or "MyRule")
        try:
            expanded = str(Path(sample).expanduser())
            self.dest_preview_label.setText(f"Preview: {expanded}")
        except Exception:
            self.dest_preview_label.setText(f"Preview: {sample}")

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Rule Name cannot be empty.")
            return

        destination = self.dest_edit.text().strip()
        if not destination:
            QMessageBox.warning(self, "Validation Error", "Destination cannot be empty.")
            return

        p_errors = validate_destination_template(destination)
        if p_errors:
            QMessageBox.warning(
                self,
                "Invalid Destination Template",
                "Destination template has errors:\n• " + "\n• ".join(p_errors),
            )
            return

        keywords = [k.strip() for k in self.keywords_edit.text().split(",") if k.strip()]
        extensions = [
            e.strip().lstrip(".").lower()
            for e in self.extensions_edit.text().split(",")
            if e.strip()
        ]

        min_sz = self.min_size_edit.text().strip() or None
        max_sz = self.max_size_edit.text().strip() or None
        conditions = Conditions(min_size=min_sz, max_size=max_sz)

        if not (keywords or extensions or conditions.any_set()):
            QMessageBox.warning(
                self,
                "Validation Error",
                "Rule must have at least one keyword, extension, or size condition.",
            )
            return

        try:
            self.rule_spec = RuleSpec(
                name=name,
                priority=self.priority_spin.value(),
                destination=destination,
                keywords=keywords,
                extensions=extensions,
                match=self.match_combo.currentData(),
                conditions=conditions,
                collision=self.collision_combo.currentData(),
                create_destinations=self.create_combo.currentData(),
            )
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid Rule Specification", str(exc))

    def get_rule_spec(self) -> RuleSpec:
        return self.rule_spec


class RulesView(QWidget):
    """Rules view with editor and real-time live test trace evaluation."""

    def __init__(self, api: GuiApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._rules_model = RulesTableModel()
        self._trace_model = TraceTableModel()
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top section: Rules Table and Management
        rules_container = QWidget()
        rules_layout = QVBoxLayout(rules_container)
        rules_layout.setContentsMargins(0, 0, 0, 0)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)
        title_label = QLabel("Rules & Organization")
        title_font = title_label.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #f8fafc;")
        header_layout.addWidget(title_label)

        subtitle_label = QLabel(
            "Define sorting rules by keyword, extension, and date. Test them in real-time below."
        )
        subtitle_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        header_layout.addWidget(subtitle_label)
        rules_layout.addLayout(header_layout)

        self.rules_table = QTableView()
        self.rules_table.setModel(self._rules_model)
        self.rules_table.setAlternatingRowColors(True)
        self.rules_table.setShowGrid(False)
        self.rules_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.rules_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.rules_table.verticalHeader().setDefaultSectionSize(32)
        self.rules_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.rules_table.horizontalHeader().setStretchLastSection(True)
        self._rules_model.rule_toggled.connect(self._on_rule_toggled)
        rules_layout.addWidget(self.rules_table)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Rule...")
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self._on_add_rule)
        btn_row.addWidget(self.add_btn)

        self.edit_btn = QPushButton("Edit Rule...")
        self.edit_btn.clicked.connect(self._on_edit_rule)
        btn_row.addWidget(self.edit_btn)

        self.remove_btn = QPushButton("Remove Rule")
        self.remove_btn.clicked.connect(self._on_remove_rule)
        btn_row.addWidget(self.remove_btn)

        self.toggle_enable_btn = QPushButton("Toggle Enable")
        self.toggle_enable_btn.clicked.connect(self._on_toggle_enable)
        btn_row.addWidget(self.toggle_enable_btn)

        btn_row.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self.refresh_btn)

        rules_layout.addLayout(btn_row)
        splitter.addWidget(rules_container)

        # Bottom section: Live Test Filename Trace
        trace_group = QGroupBox("Test Filename (Live Real-Time Match Trace)")
        trace_layout = QVBoxLayout(trace_group)
        trace_layout.setSpacing(10)

        input_row = QHBoxLayout()
        lbl_test = QLabel("Test Filename:")
        lbl_test.setStyleSheet("color: #cbd5e1; font-weight: 500;")
        input_row.addWidget(lbl_test)
        self.test_input = QLineEdit()
        self.test_input.setPlaceholderText(
            "Type a filename (e.g. invoice_march.pdf) to see matching rules instantly..."
        )
        self.test_input.textChanged.connect(self.evaluate_trace)
        input_row.addWidget(self.test_input)
        trace_layout.addLayout(input_row)

        self.trace_table = QTableView()
        self.trace_table.setModel(self._trace_model)
        self.trace_table.setAlternatingRowColors(True)
        self.trace_table.setShowGrid(False)
        self.trace_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.trace_table.verticalHeader().setDefaultSectionSize(30)
        self.trace_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.trace_table.horizontalHeader().setStretchLastSection(True)
        trace_layout.addWidget(self.trace_table)

        splitter.addWidget(trace_group)
        main_layout.addWidget(splitter)

    def refresh(self) -> None:
        """Fetch and reload rules."""

        def _fetch() -> list[RuleSpec]:
            cfg = self.api.get_runtime_config(force_reload=True)
            return list(cfg.all_rules)

        def _apply(rules: list[RuleSpec]) -> None:
            self._rules_model.set_rules(rules)
            self.evaluate_trace(self.test_input.text())

        run_in_worker(_fetch, on_result=_apply)

    def evaluate_trace(self, text: str) -> None:
        """Run live trace for text string."""
        raw_text = text.strip()
        if not raw_text:
            self._trace_model.set_traces([])
            return

        traces = self.api.rules_trace(raw_text)
        self._trace_model.set_traces(traces)

    def _on_rule_toggled(self, rule_name: str, enabled: bool) -> None:
        def _do() -> None:
            if enabled:
                self.api.config_enable_rule(rule_name)
            else:
                self.api.config_disable_rule(rule_name)

        run_in_worker(_do, on_result=lambda _: self.refresh())

    def _on_toggle_enable(self) -> None:
        selected_indexes = self.rules_table.selectionModel().selectedRows()
        if not selected_indexes:
            return
        row = selected_indexes[0].row()
        rule = self._rules_model.get_rule(row)
        if rule:
            self._on_rule_toggled(rule.name, not rule.enabled)

    def _on_add_rule(self) -> None:
        dialog = RuleEditorDialog(None, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule_spec = dialog.get_rule_spec()

            def _do_add() -> None:
                self.api.config_save_rule(rule_spec, is_new=True)

            def _on_err(exc: Exception) -> None:
                msg = str(exc)
                if isinstance(exc, ConfigError) and exc.problems:
                    msg += ":\n\n• " + "\n• ".join(exc.problems)
                QMessageBox.critical(self, "Error Adding Rule", msg)

            run_in_worker(_do_add, on_result=lambda _: self.refresh(), on_error=_on_err)

    def _on_edit_rule(self) -> None:
        selected_indexes = self.rules_table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "Selection Required", "Please select a rule to edit.")
            return

        row = selected_indexes[0].row()
        rule = self._rules_model.get_rule(row)
        if not rule:
            return

        dialog = RuleEditorDialog(rule, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule_spec = dialog.get_rule_spec()

            def _do_edit() -> None:
                self.api.config_save_rule(rule_spec, is_new=False)

            def _on_err(exc: Exception) -> None:
                msg = str(exc)
                if isinstance(exc, ConfigError) and exc.problems:
                    msg += ":\n\n• " + "\n• ".join(exc.problems)
                QMessageBox.critical(self, "Error Editing Rule", msg)

            run_in_worker(_do_edit, on_result=lambda _: self.refresh(), on_error=_on_err)

    def _on_remove_rule(self) -> None:
        selected_indexes = self.rules_table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "Selection Required", "Please select a rule to remove.")
            return

        row = selected_indexes[0].row()
        rule = self._rules_model.get_rule(row)
        if not rule:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Remove rule '{rule.name}' permanently from configuration?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:

            def _do_del() -> None:
                self.api.config_remove_rule(rule.name)

            def _on_err(exc: Exception) -> None:
                msg = str(exc)
                if isinstance(exc, ConfigError) and exc.problems:
                    msg += ":\n\n• " + "\n• ".join(exc.problems)
                QMessageBox.critical(self, "Error Removing Rule", msg)

            run_in_worker(_do_del, on_result=lambda _: self.refresh(), on_error=_on_err)


__all__ = [
    "RuleEditorDialog",
    "RulesView",
    "TraceTableModel",
]
