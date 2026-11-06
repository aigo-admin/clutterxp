"""Test A83: Folders view edits watch folders and per-folder settings."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from clutterxp.config.schema import WatchEntry
from clutterxp.gui.api import GuiApi
from clutterxp.gui.views.folders import FolderSettingsDialog, FoldersView


def test_A83_folders_view_edits_watch_folders_and_settings(
    qtbot: QtBot,
    gui_api: GuiApi,
    tmp_path: Path,
) -> None:
    view = FoldersView(gui_api)
    qtbot.addWidget(view)

    # Wait for initial load
    qtbot.waitUntil(lambda: view._model.rowCount() == 1, timeout=5000)
    initial_entry = view._model.get_entry(0)
    assert initial_entry is not None
    assert initial_entry.recursive is False

    # 1. Edit per-folder settings using FolderSettingsDialog
    dialog = FolderSettingsDialog(initial_entry)
    qtbot.addWidget(dialog)

    dialog.recursive_cb.setChecked(True)
    dialog.settle_spin.setValue(5.5)
    dialog.ignore_edit.setText("*.tmp, *.bak")
    dialog.rules_edit.setText("Invoices")

    updates = dialog.get_updates()
    assert updates["recursive"] is True
    assert updates["settle_seconds"] == 5.5
    assert updates["ignore"] == ["*.tmp", "*.bak"]
    assert updates["rules"] == ["Invoices"]

    # Apply updates via api
    gui_api.config_edit_watch(initial_entry.path, updates)
    view.refresh()

    qtbot.waitUntil(
        lambda: view._model.get_entry(0) is not None and view._model.get_entry(0).recursive is True,  # type: ignore
        timeout=5000,
    )
    updated_entry = view._model.get_entry(0)
    assert updated_entry is not None
    assert updated_entry.recursive is True
    assert updated_entry.settle_seconds == 5.5
    assert updated_entry.ignore == ["*.tmp", "*.bak"]

    # 2. Add a new watch folder
    new_dir = tmp_path / "ExtraWatch"
    new_dir.mkdir(parents=True, exist_ok=True)
    gui_api.config_add_watch(WatchEntry(path=str(new_dir), recursive=True))
    view.refresh()

    qtbot.waitUntil(lambda: view._model.rowCount() == 2, timeout=5000)
    paths_in_model = [
        entry.path
        for i in range(view._model.rowCount())
        if (entry := view._model.get_entry(i)) is not None
    ]
    assert str(new_dir) in paths_in_model

    # 3. Remove a watch folder
    gui_api.config_remove_watch(str(new_dir))
    view.refresh()

    qtbot.waitUntil(lambda: view._model.rowCount() == 1, timeout=5000)

    # 4. Start/Stop watcher button
    view.watcher_toggle_btn.click()
    # Status toggles
    assert view.watcher_status_label.text() != ""
    gui_api.watcher_stop()
