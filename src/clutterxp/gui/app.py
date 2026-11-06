"""Main application window and entry point for ClutterXP GUI.

Features:
- Single-instance guard via flock on paths.state_dir() / 'gui.lock'
- Tabbed interface containing Dashboard, Folders, Rules, Preview, History, Duplicates, and Settings
- Inter-view navigation signals
- Clean SIGINT and shutdown handling
"""

from __future__ import annotations

import fcntl
import signal
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from clutterxp import paths
from clutterxp.gui.api import GuiApi
from clutterxp.gui.theme import apply_theme
from clutterxp.gui.views import (
    DashboardView,
    DuplicatesView,
    FoldersView,
    HistoryView,
    PreviewView,
    RulesView,
    SettingsView,
)


class ClutterXPMainWindow(QMainWindow):
    """Main application window containing all view tabs."""

    def __init__(self, api: GuiApi | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api if api is not None else GuiApi()
        self.setWindowTitle("ClutterXP - Safe, Intelligent File Organizer")
        self.resize(1080, 720)
        self.setMinimumSize(880, 580)

        app_instance = QApplication.instance()
        if app_instance is not None:
            apply_theme(app_instance)

        self._init_ui()

    def _init_ui(self) -> None:
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Create Views
        self.dashboard_view = DashboardView(self.api)
        self.folders_view = FoldersView(self.api)
        self.rules_view = RulesView(self.api)
        self.preview_view = PreviewView(self.api)
        self.history_view = HistoryView(self.api)
        self.duplicates_view = DuplicatesView(self.api)
        self.settings_view = SettingsView(self.api)

        # Add tabs
        self.tabs.addTab(self.dashboard_view, "Dashboard")
        self.tabs.addTab(self.folders_view, "Folders")
        self.tabs.addTab(self.rules_view, "Rules")
        self.tabs.addTab(self.preview_view, "Preview & Organize")
        self.tabs.addTab(self.history_view, "History")
        self.tabs.addTab(self.duplicates_view, "Duplicates")
        self.tabs.addTab(self.settings_view, "Settings")

        # Connect inter-view navigation
        self.dashboard_view.navigate_requested.connect(self.navigate_to)
        self.preview_view.navigate_requested.connect(self.navigate_to)
        self.preview_view.open_rule_requested.connect(self._on_open_rule_in_editor)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        # Menu bar
        self._init_menu()

    def _init_menu(self) -> None:
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        refresh_action = QAction("&Refresh All", self)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self.refresh_all)
        file_menu.addAction(refresh_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View Menu
        view_menu = menubar.addMenu("&View")
        for i in range(self.tabs.count()):
            tab_name = self.tabs.tabText(i)
            action = QAction(f"Show {tab_name}", self)
            action.triggered.connect(lambda _, idx=i: self.tabs.setCurrentIndex(idx))
            view_menu.addAction(action)

        # Help Menu
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About ClutterXP", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def navigate_to(self, view_name: str) -> None:
        """Switch to a specified tab."""
        mapping = {
            "dashboard": 0,
            "folders": 1,
            "rules": 2,
            "preview": 3,
            "history": 4,
            "duplicates": 5,
            "settings": 6,
        }
        idx = mapping.get(view_name.lower())
        if idx is not None:
            self.tabs.setCurrentIndex(idx)
            current_widget = self.tabs.currentWidget()
            if hasattr(current_widget, "refresh"):
                current_widget.refresh()

    def _on_open_rule_in_editor(self, rule_name: str) -> None:
        """Switch to rules tab and filter/select rule."""
        self.navigate_to("rules")
        self.rules_view.test_input.setText(rule_name)

    def refresh_all(self) -> None:
        """Refresh current view and state."""
        current_widget = self.tabs.currentWidget()
        if hasattr(current_widget, "refresh"):
            current_widget.refresh()
        self.status_bar.showMessage("Refreshed", 2000)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About ClutterXP",
            "<h3>ClutterXP (v1.1)</h3>"
            "<p>Configurable, safe, lightweight file organizer.</p>"
            "<p>Never overwrites, never deletes without verification, everything is undoable.</p>",
        )


def acquire_gui_lock(lock_path: Path | None = None) -> Any:
    """Acquire single-instance file lock using flock."""
    path = lock_path if lock_path is not None else (paths.state_dir() / "gui.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (BlockingIOError, OSError):
        f.close()
        return None


def main() -> None:
    """Entry point for GUI application."""
    lock_file = acquire_gui_lock()
    if lock_file is None:
        sys.stderr.write("ClutterXP GUI is already running.\n")
        sys.exit(0)

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("ClutterXP")
    apply_theme(app)

    # Hook up SIGINT to cleanly exit
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sig_timer = QTimer()
    sig_timer.timeout.connect(lambda: None)  # Lets python interpreter handle signals
    sig_timer.start(500)

    window = ClutterXPMainWindow()
    window.show()

    try:
        sys.exit(app.exec())
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


__all__ = [
    "ClutterXPMainWindow",
    "acquire_gui_lock",
    "main",
]
