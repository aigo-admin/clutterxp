"""Additional tests for GuiApi and views edge cases."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from clutterxp.config.schema import RuleSpec, WatchEntry
from clutterxp.gui.api import (
    GuiApi,
    add_watch_folder,
    edit_watch_folder,
    remove_watch_folder,
)
from clutterxp.gui.app import ClutterXPMainWindow, acquire_gui_lock
from clutterxp.gui.views.folders import FolderSettingsDialog, FoldersView
from clutterxp.gui.views.preview import PreviewView
from clutterxp.gui.views.rules import RuleEditorDialog, RulesView


def test_gui_api_config_operations(gui_api: GuiApi, tmp_path: Path) -> None:
    # Test add/edit/remove watch folder directly and via helpers
    watch_p = tmp_path / "ExtraWatch"
    watch_p.mkdir()

    add_watch_folder(
        gui_api.config_path,
        {"path": str(watch_p), "recursive": True, "settle_seconds": 5.0},
    )
    cfg = gui_api.get_runtime_config()
    assert any(w.path == str(watch_p) for w in cfg.watch_entries)

    edit_watch_folder(gui_api.config_path, str(watch_p), {"settle_seconds": 10.0})
    gui_api.reload_config()
    cfg = gui_api.get_runtime_config()
    entry = next(w for w in cfg.watch_entries if w.path == str(watch_p))
    assert entry.settle_seconds == 10.0

    remove_watch_folder(gui_api.config_path, str(watch_p))
    gui_api.reload_config()
    cfg = gui_api.get_runtime_config()
    assert not any(w.path == str(watch_p) for w in cfg.watch_entries)

    # Test api convenience wrappers
    gui_api.config_add_watch({"path": str(watch_p), "recursive": False, "settle_seconds": 2.0})
    gui_api.config_edit_watch(str(watch_p), {"settle_seconds": 4.0})
    gui_api.config_remove_watch(str(watch_p))

    # Test rule management
    rule = RuleSpec(
        name="TempRule",
        priority=99,
        keywords=["temp"],
        extensions=["tmp"],
        destination=str(tmp_path / "TempDest"),
    )
    gui_api.config_save_rule(rule, is_new=True)
    assert any(r.name == "TempRule" for r in gui_api.get_runtime_config().rules)

    gui_api.config_disable_rule("TempRule")
    assert any(r.name == "TempRule" for r in gui_api.get_runtime_config().disabled_rules)

    gui_api.config_enable_rule("TempRule")
    assert any(r.name == "TempRule" for r in gui_api.get_runtime_config().rules)

    gui_api.config_remove_rule("TempRule")
    assert not any(r.name == "TempRule" for r in gui_api.get_runtime_config().rules)
    assert not any(r.name == "TempRule" for r in gui_api.get_runtime_config().disabled_rules)

    # Test config_validate
    errs, warns = gui_api.config_validate()
    assert isinstance(errs, list)
    assert isinstance(warns, list)

    # Test rules_trace with non-existent path
    trace = gui_api.rules_trace(tmp_path / "nonexistent_invoice_123.pdf")
    assert len(trace) > 0

    # Test today move count and history_get_op
    assert gui_api.get_today_move_count() == 0
    assert gui_api.history_get_op(999999) is None
    assert gui_api.undo_last_batch() == []


def test_folders_view_and_dialog(qtbot: QtBot, gui_api: GuiApi, tmp_path: Path) -> None:
    # Ensure at least one folder exists in view
    gui_api.config_add_watch({"path": str(tmp_path / "Watched")})

    view = FoldersView(gui_api)
    qtbot.addWidget(view)
    view.show()

    # Wait for background worker to populate entries
    qtbot.waitUntil(lambda: view._model.rowCount() > 0, timeout=2000)

    # Test dialog for folder settings
    entry = WatchEntry(path=str(tmp_path / "MyFolder"), recursive=False, settle_seconds=3.0)
    dialog = FolderSettingsDialog(entry=entry, parent=view)
    qtbot.addWidget(dialog)
    dialog.recursive_cb.setChecked(True)
    dialog.settle_spin.setValue(4.5)
    updates = dialog.get_updates()
    assert updates["recursive"] is True
    assert updates["settle_seconds"] == 4.5

    # Test selecting folder in table
    assert view._model.rowCount() > 0
    view.table.selectRow(0)
    assert view.edit_btn.isEnabled()
    assert view.remove_btn.isEnabled()


def test_rules_view_and_dialog(qtbot: QtBot, gui_api: GuiApi, tmp_path: Path) -> None:
    view = RulesView(gui_api)
    qtbot.addWidget(view)
    view.show()

    # Test RuleEditorDialog
    rule = RuleSpec(
        name="NewRule",
        priority=15,
        keywords=["a", "b"],
        extensions=["txt"],
        destination=str(tmp_path / "Dest"),
    )
    dialog = RuleEditorDialog(rule=rule, parent=view)
    qtbot.addWidget(dialog)
    dialog._on_save()
    new_spec = dialog.get_rule_spec()
    assert new_spec.name == "NewRule"
    assert new_spec.priority == 15
    assert list(new_spec.keywords) == ["a", "b"]
    assert new_spec.destination == str(tmp_path / "Dest")

    # Test select rule in table
    view.rules_table.selectRow(0)
    assert view.edit_btn.isEnabled()
    assert view.remove_btn.isEnabled()


def test_preview_view_filtering(qtbot: QtBot, gui_api: GuiApi, tmp_path: Path) -> None:
    view = PreviewView(gui_api)
    qtbot.addWidget(view)
    view.show()

    # Test toggle include all
    view.select_all_btn.click()
    view.deselect_all_btn.click()

    # Test status filter combo
    view.filter_combo.setCurrentIndex(1)
    view.filter_combo.setCurrentIndex(0)


def test_main_window_and_lock(qtbot: QtBot, gui_api: GuiApi, tmp_path: Path) -> None:
    # Test acquire_gui_lock
    lock_file = tmp_path / "gui.lock"
    lock_fd = acquire_gui_lock(lock_file)
    assert lock_fd is not None
    # Second acquisition fails
    second_lock = acquire_gui_lock(lock_file)
    assert second_lock is None

    # Test MainWindow
    win = ClutterXPMainWindow(api=gui_api)
    qtbot.addWidget(win)
    win.show()

    assert win.tabs.count() == 7
    # Switch through tabs
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        assert win.tabs.currentIndex() == i
