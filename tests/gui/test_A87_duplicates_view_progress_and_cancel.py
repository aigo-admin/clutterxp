"""Test A87: Duplicates view runs with progress and cancel."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from clutterxp.gui.api import GuiApi
from clutterxp.gui.views.duplicates import DuplicatesView
from tests.conftest import MakeTreeFn


def test_A87_duplicates_view_progress_and_cancel(
    qtbot: QtBot,
    gui_api: GuiApi,
    tmp_path: Path,
    make_tree: MakeTreeFn,
) -> None:
    # 1. Setup duplicates fixture
    dup_dir = tmp_path / "ScanFolder"
    dup_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "doc1.txt": "same duplicate content",
            "doc2.txt": "same duplicate content",
            "unique.txt": "completely unique file",
        },
        base=dup_dir,
    )

    view = DuplicatesView(gui_api)
    qtbot.addWidget(view)
    view.show()

    # Clear default folders and add our test folder
    view.folders_list.clear()
    view.folders_list.addItem(str(dup_dir))

    # 2. Test successful scan with progress updates
    with (
        patch.object(QMessageBox, "critical", return_value=QMessageBox.StandardButton.Ok),
        patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
        patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok),
    ):
        view.start_scan()
        assert view.cancel_btn.isEnabled()

        # Wait until scan completes and groups are populated
        qtbot.waitUntil(lambda: view._model.rowCount() >= 1, timeout=5000)
        assert view.scan_btn.isEnabled()
        assert not view.cancel_btn.isEnabled()

        # Verify duplicate group
        group = view._model.get_group(0)
        assert group is not None
        assert group.kind == "exact"
        assert len(group.files) == 2

    # 3. Test cancellation
    # Introduce slow detector scan or trigger cancel immediately
    def _slow_scan(*args, **kwargs):  # type: ignore
        cancel = kwargs.get("cancel")
        for _ in range(50):
            if cancel is not None and cancel.is_set():
                return []
            time.sleep(0.05)
        return []

    with patch.object(gui_api, "duplicates_scan", side_effect=_slow_scan):
        view.start_scan()
        assert view.cancel_btn.isEnabled()
        assert not view._cancel_token.is_cancelled

        # Click cancel
        view.cancel_btn.click()
        assert view._cancel_token.is_cancelled
        assert not view.cancel_btn.isEnabled()
        assert view.scan_btn.isEnabled()
