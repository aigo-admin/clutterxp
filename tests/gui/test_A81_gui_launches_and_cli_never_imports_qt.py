"""Test A81: GUI launches cleanly and CLI never imports Qt."""

from __future__ import annotations

import sys

from pytestqt.qtbot import QtBot

from clutterxp.gui.api import GuiApi
from clutterxp.gui.app import ClutterXPMainWindow, acquire_gui_lock
from tests.conftest import EnvPaths


def test_A81_gui_launches_and_cli_never_imports_qt(
    qtbot: QtBot,
    gui_api: GuiApi,
    env: EnvPaths,
) -> None:
    # 1. Critical Invariant: Importing CLI app must NEVER import PySide6 or Qt modules
    # Run in a clean isolated subprocess to prove CLI never imports Qt
    import subprocess

    cmd = (
        "import sys; "
        "from clutterxp.cli import app; "
        "assert 'PySide6' not in sys.modules, 'PySide6 leaked into CLI'; "
        "assert not any('Qt' in m for m in sys.modules), 'Qt leaked into sys.modules'"
    )
    res = subprocess.run([sys.executable, "-c", cmd], capture_output=True, text=True)
    assert res.returncode == 0, f"CLI imported Qt! stderr: {res.stderr}"

    # 2. Verify GUI main window launches cleanly and creates all views
    window = ClutterXPMainWindow(gui_api)
    qtbot.addWidget(window)
    window.show()

    assert window.tabs.count() == 7
    tab_titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert "Dashboard" in tab_titles
    assert "Folders" in tab_titles
    assert "Rules" in tab_titles
    assert "Preview & Organize" in tab_titles
    assert "History" in tab_titles
    assert "Duplicates" in tab_titles
    assert "Settings" in tab_titles

    # 3. Test single-instance lock guard
    lock_file = acquire_gui_lock(env.state_dir / "gui.lock")
    assert lock_file is not None

    second_lock = acquire_gui_lock(env.state_dir / "gui.lock")
    assert second_lock is None  # Second instance is refused

    lock_file.close()
