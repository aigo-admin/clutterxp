"""Test A82: Dashboard shows watcher state, stats, and recent operations."""

from __future__ import annotations

from pathlib import Path

from pytestqt.qtbot import QtBot

from clutterxp.gui.api import GuiApi
from clutterxp.gui.views.dashboard import DashboardView
from clutterxp.models import FileInfo, MoveResult, MoveStrategy


def test_A82_dashboard_shows_watcher_and_recent_operations(
    qtbot: QtBot,
    gui_api: GuiApi,
    tmp_path: Path,
) -> None:
    # 1. Record 2 operations in history
    batch_id = gui_api.history.begin_batch("test", folder=tmp_path)
    file_path = tmp_path / "test1.pdf"
    file_path.write_text("content")
    info = FileInfo.from_path(file_path)

    op_id = gui_api.history.begin_op(batch_id, file_path, "Invoices", info)
    dest_path = tmp_path / "dest1.pdf"
    gui_api.history.finish_op(
        op_id,
        MoveResult(ok=True, src=file_path, dst=dest_path, strategy=MoveStrategy.RENAME_NOREPLACE),
    )

    # 2. Instantiate Dashboard view
    view = DashboardView(gui_api)
    qtbot.addWidget(view)

    # Wait for background worker to populate UI
    def _check_ui() -> bool:
        has_status = "STOPPED" in view.watcher_status_label.text()
        has_count = view.folders_count_label.text() == "1"
        return has_status and has_count

    qtbot.waitUntil(_check_ui, timeout=5000)

    # Verify watcher state and folder count
    assert "STOPPED" in view.watcher_status_label.text()
    assert view.folders_count_label.text() == "1"
    assert int(view.today_moves_label.text()) >= 1

    # Verify recent operations table has our recorded move
    assert view._history_model.rowCount() >= 1
    rec = view._history_model.get_record(0)
    assert rec is not None
    assert rec.rule_name == "Invoices"
    assert rec.dst_path == dest_path

    # Verify quick action buttons exist and are enabled
    assert view.scan_btn.isEnabled()
    assert view.organize_btn.isEnabled()
    assert view.undo_last_btn.isEnabled()
