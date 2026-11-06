"""Settings view for global configuration, editor launch, and live validation.

Displays global settings values, config file path, provides an 'Open in Editor' shortcut,
and runs live config validation displaying errors and warnings.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clutterxp.config.runtime import RuntimeConfig
from clutterxp.gui.api import GuiApi
from clutterxp.gui.workers import run_in_worker


class SettingsView(QWidget):
    """View displaying global settings and live validation feedback."""

    def __init__(self, api: GuiApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Title
        title_label = QLabel("Settings & Configuration")
        title_font = title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)

        # Config File Card
        file_box = QGroupBox("Configuration File")
        file_layout = QHBoxLayout(file_box)

        self.path_label = QLabel(str(self.api.config_path))
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_layout.addWidget(self.path_label, stretch=1)

        self.open_editor_btn = QPushButton("Open in Text Editor")
        self.open_editor_btn.clicked.connect(self._on_open_editor)
        file_layout.addWidget(self.open_editor_btn)

        self.reload_btn = QPushButton("Reload")
        self.reload_btn.clicked.connect(self.refresh)
        file_layout.addWidget(self.reload_btn)

        main_layout.addWidget(file_box)

        # Global Settings Summary Box
        settings_box = QGroupBox("Global Settings (from config.yaml)")
        form = QFormLayout(settings_box)

        self.collision_label = QLabel()
        form.addRow("Collision Policy:", self.collision_label)

        self.create_label = QLabel()
        form.addRow("Create Destinations:", self.create_label)

        self.match_label = QLabel()
        form.addRow("Default Match Mode:", self.match_label)

        self.settle_label = QLabel()
        form.addRow("Settle Time:", self.settle_label)

        self.threshold_label = QLabel()
        form.addRow("Confirmation Threshold:", self.threshold_label)

        self.hidden_label = QLabel()
        form.addRow("Skip Hidden Files:", self.hidden_label)

        main_layout.addWidget(settings_box)

        # Live Validation Panel
        val_box = QGroupBox("Configuration Validation Panel")
        val_layout = QVBoxLayout(val_box)

        status_row = QHBoxLayout()
        self.val_status_label = QLabel("Status: Checking...")
        status_font = self.val_status_label.font()
        status_font.setBold(True)
        self.val_status_label.setFont(status_font)
        status_row.addWidget(self.val_status_label)

        status_row.addStretch()

        self.revalidate_btn = QPushButton("Re-validate Now")
        self.revalidate_btn.clicked.connect(self._run_validation)
        status_row.addWidget(self.revalidate_btn)
        val_layout.addLayout(status_row)

        self.issues_list = QListWidget()
        val_layout.addWidget(self.issues_list)

        main_layout.addWidget(val_box, stretch=1)

    def refresh(self) -> None:
        """Fetch and reload settings and validation."""

        def _fetch() -> tuple[RuntimeConfig, tuple[list[str], list[str]]]:
            cfg = self.api.reload_config()
            val_res = self.api.config_validate()
            return cfg, val_res

        def _apply(res: tuple[RuntimeConfig, tuple[list[str], list[str]]]) -> None:
            cfg, val_res = res
            errors, warnings = val_res

            # Update settings labels
            s = cfg.settings
            self.collision_label.setText(str(s.collision.value))
            self.create_label.setText(str(s.create_destinations.value))
            self.match_label.setText(str(s.match.value))
            self.settle_label.setText(f"{s.settle_seconds}s")
            self.threshold_label.setText(f"{s.confirm_threshold} files")
            self.hidden_label.setText("Yes" if s.skip_hidden else "No")

            # Update validation panel
            self.issues_list.clear()
            if errors:
                self.val_status_label.setText(
                    f"Configuration Invalid ({len(errors)} error(s), {len(warnings)} warning(s))"
                )
                self.val_status_label.setStyleSheet("color: #b30000;")
            elif warnings:
                self.val_status_label.setText(
                    f"Configuration Valid with {len(warnings)} Warning(s)"
                )
                self.val_status_label.setStyleSheet("color: #b37400;")
            else:
                self.val_status_label.setText("Configuration is Valid (No issues detected)")
                self.val_status_label.setStyleSheet("color: #2b7a0b;")

            for err in errors:
                item = QListWidgetItem(f"[ERROR] {err}")
                item.setForeground(QLabel().palette().color(QLabel().foregroundRole()))
                self.issues_list.addItem(item)

            for warn in warnings:
                item = QListWidgetItem(f"[WARNING] {warn}")
                self.issues_list.addItem(item)

        run_in_worker(_fetch, on_result=_apply)

    def _run_validation(self) -> None:
        self.refresh()

    def _on_open_editor(self) -> None:
        cfg_path = self.api.config_path
        if cfg_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(cfg_path)))


__all__ = ["SettingsView"]
