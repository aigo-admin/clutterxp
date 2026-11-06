"""Dashboard view for ClutterXP GUI.

Displays watcher state, quick summary statistics, last 5 operations,
and quick actions (Scan, Organize, Undo Last Batch).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from clutterxp.gui.api import GuiApi
from clutterxp.gui.models_qt import HistoryTableModel
from clutterxp.gui.workers import run_in_worker


class DashboardView(QWidget):
    """Main dashboard overview."""

    navigate_requested = Signal(str)  # "preview", "folders", "rules", "history", etc.

    def __init__(self, api: GuiApi, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._history_model = HistoryTableModel()
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # Title
        title_label = QLabel("ClutterXP Dashboard")
        title_font = title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)

        # Top row: Watcher Card & Stats Grid
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        # Watcher State Card
        self.watcher_group = QGroupBox("Watcher Daemon")
        watcher_layout = QVBoxLayout(self.watcher_group)
        watcher_layout.setSpacing(8)

        self.watcher_status_label = QLabel("Status: Checking...")
        status_font = self.watcher_status_label.font()
        status_font.setBold(True)
        self.watcher_status_label.setFont(status_font)
        watcher_layout.addWidget(self.watcher_status_label)

        self.watcher_folders_label = QLabel("Watched Folders: None")
        self.watcher_folders_label.setWordWrap(True)
        watcher_layout.addWidget(self.watcher_folders_label)

        self.watcher_toggle_btn = QPushButton("Toggle Watcher")
        self.watcher_toggle_btn.clicked.connect(self._on_toggle_watcher)
        watcher_layout.addWidget(self.watcher_toggle_btn)
        watcher_layout.addStretch()

        top_row.addWidget(self.watcher_group, stretch=1)

        # Quick Stats Card
        stats_group = QGroupBox("Quick Stats")
        stats_layout = QGridLayout(stats_group)
        stats_layout.setSpacing(12)

        stats_layout.addWidget(QLabel("Configured Folders:"), 0, 0)
        self.folders_count_label = QLabel("0")
        self.folders_count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        stats_layout.addWidget(self.folders_count_label, 0, 1)

        stats_layout.addWidget(QLabel("Today's Moves:"), 1, 0)
        self.today_moves_label = QLabel("0")
        self.today_moves_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        stats_layout.addWidget(self.today_moves_label, 1, 1)

        stats_layout.addWidget(QLabel("Active Rules:"), 2, 0)
        self.rules_count_label = QLabel("0")
        self.rules_count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        stats_layout.addWidget(self.rules_count_label, 2, 1)

        stats_group.setLayout(stats_layout)
        top_row.addWidget(stats_group, stretch=1)

        # Quick Actions Card
        actions_group = QGroupBox("Quick Actions")
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setSpacing(8)

        self.scan_btn = QPushButton("Scan Folders")
        self.scan_btn.clicked.connect(lambda: self.navigate_requested.emit("preview"))
        actions_layout.addWidget(self.scan_btn)

        self.organize_btn = QPushButton("Organize (Preview)")
        self.organize_btn.clicked.connect(lambda: self.navigate_requested.emit("preview"))
        actions_layout.addWidget(self.organize_btn)

        self.undo_last_btn = QPushButton("Undo Last Batch")
        self.undo_last_btn.clicked.connect(self._on_undo_last_batch)
        actions_layout.addWidget(self.undo_last_btn)

        actions_layout.addStretch()
        top_row.addWidget(actions_group, stretch=1)

        main_layout.addLayout(top_row)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(line)

        # Bottom section: Recent Operations
        recent_group = QGroupBox("Recent Operations (Last 5)")
        recent_layout = QVBoxLayout(recent_group)

        self.recent_table = QTableView()
        self.recent_table.setModel(self._history_model)
        self.recent_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.recent_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.recent_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.recent_table.horizontalHeader().setStretchLastSection(True)
        recent_layout.addWidget(self.recent_table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.view_history_btn = QPushButton("View Full History")
        self.view_history_btn.clicked.connect(lambda: self.navigate_requested.emit("history"))
        btn_row.addWidget(self.view_history_btn)
        recent_layout.addLayout(btn_row)

        main_layout.addWidget(recent_group, stretch=1)

    def refresh(self) -> None:
        """Fetch dashboard state in background worker."""

        def _fetch() -> dict[str, Any]:
            status = self.api.watcher_status()
            cfg = self.api.get_runtime_config()
            today_moves = self.api.get_today_move_count()
            recent_ops = self.api.history_list(limit=5)
            return {
                "watcher_status": status,
                "config": cfg,
                "today_moves": today_moves,
                "recent_ops": recent_ops,
            }

        run_in_worker(
            _fetch,
            on_result=self._apply_dashboard_data,
        )

    def _apply_dashboard_data(self, data: dict[str, Any]) -> None:
        """Update UI with fetched data on main thread."""
        status = data["watcher_status"]
        cfg = data["config"]
        today_moves = data["today_moves"]
        recent_ops = data["recent_ops"]

        # Watcher State
        if status == "running":
            self.watcher_status_label.setText("Status: RUNNING")
            self.watcher_toggle_btn.setText("Stop Watcher")
        else:
            self.watcher_status_label.setText("Status: STOPPED")
            self.watcher_toggle_btn.setText("Start Watcher")

        watch_paths = [w.path for w in cfg.watch_entries]
        if watch_paths:
            self.watcher_folders_label.setText(
                f"Watching {len(watch_paths)} folder(s): {', '.join(watch_paths)}"
            )
        else:
            self.watcher_folders_label.setText("Watched Folders: None configured")

        # Stats
        self.folders_count_label.setText(str(len(cfg.watch_entries)))
        self.today_moves_label.setText(str(today_moves))
        self.rules_count_label.setText(str(len(cfg.rules)))

        # Recent Operations Table
        self._history_model.set_records(recent_ops)

    def _on_toggle_watcher(self) -> None:
        current_status = self.api.watcher_status()
        if current_status == "running":
            self.api.watcher_stop()
        else:
            self.api.watcher_start()
        self.refresh()

    def _on_undo_last_batch(self) -> None:
        reply = QMessageBox.question(
            self,
            "Confirm Undo",
            "Are you sure you want to undo the last batch of file moves?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:

            def _do_undo() -> Any:
                return self.api.undo_last_batch()

            def _on_done(outcomes: Any) -> None:
                if not outcomes:
                    QMessageBox.information(self, "Undo", "No previous batches to undo.")
                else:
                    succeeded = sum(1 for o in outcomes if o.ok)
                    failed = len(outcomes) - succeeded
                    if failed > 0:
                        reasons = "\n".join(
                            f"- {o.message or o.code.value}" for o in outcomes if not o.ok
                        )
                        QMessageBox.warning(
                            self,
                            "Undo Completed with Refusals",
                            f"Restored {succeeded} files.\n{failed} files refused:\n{reasons}",
                        )
                    else:
                        QMessageBox.information(
                            self,
                            "Undo Successful",
                            f"Successfully restored {succeeded} file(s).",
                        )
                self.refresh()

            run_in_worker(_do_undo, on_result=_on_done)


__all__ = ["DashboardView"]
