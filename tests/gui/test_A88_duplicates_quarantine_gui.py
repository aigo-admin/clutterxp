"""Test A88: Duplicates view quarantine workflow.

Covers exact duplicates, preview dialog, execution via SafeMover, files moved without deletion,
journal recording, undoability from history, and prevention of non-exact group quarantine.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from clutterxp import paths
from clutterxp.gui.api import GuiApi
from clutterxp.gui.views.duplicates import DuplicatesView, QuarantinePreviewDialog
from clutterxp.models import DuplicateGroup, FileInfo
from tests.conftest import MakeTreeFn


def test_A88_duplicates_quarantine_gui(
    qtbot: QtBot,
    gui_api: GuiApi,
    tmp_path: Path,
    make_tree: MakeTreeFn,
) -> None:
    # 1. Setup exact duplicates
    scan_dir = tmp_path / "ScanDir"
    scan_dir.mkdir(parents=True, exist_ok=True)

    make_tree(
        {
            "original.txt": "identical content for testing quarantine",
            "duplicate.txt": "identical content for testing quarantine",
        },
        base=scan_dir,
    )

    f_orig = scan_dir / "original.txt"
    f_dupe = scan_dir / "duplicate.txt"

    # Ensure original.txt is strictly older
    os.utime(f_orig, (1000, 1000))
    os.utime(f_dupe, (2000, 2000))

    # 2. Open DuplicatesView
    view = DuplicatesView(gui_api)
    qtbot.addWidget(view)
    view.show()

    view.folders_list.clear()
    view.folders_list.addItem(str(scan_dir))

    # Run scan to detect the exact duplicates
    with (
        patch.object(QMessageBox, "critical", return_value=QMessageBox.StandardButton.Ok),
        patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
        patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok),
    ):
        view.start_scan()
        qtbot.waitUntil(lambda: view._model.rowCount() == 1, timeout=5000)

    group = view._model.get_group(0)
    assert group is not None
    assert group.kind == "exact"
    assert len(group.files) == 2

    # 3. Test QuarantinePreviewDialog directly
    quarantine_dir = paths.state_dir() / "quarantine"
    plan = gui_api.duplicates_plan_quarantine([group], quarantine_dir=quarantine_dir)
    assert plan.total_moves == 1
    assert len(plan.kept_files) == 1
    assert plan.kept_files[0].path == f_orig
    assert plan.actions[0].file.path == f_dupe

    dialog = QuarantinePreviewDialog(plan, view)
    qtbot.addWidget(dialog)
    dialog.show()

    # 4. Trigger Quarantine from DuplicatesView
    with (
        patch.object(QDialog, "exec", return_value=QDialog.DialogCode.Accepted),
        patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok),
    ):
        view.quarantine_btn.click()

        # Wait until file is moved to quarantine directory
        qtbot.waitUntil(lambda: len(list(quarantine_dir.glob("*"))) == 1, timeout=5000)

    # Verify original is kept in place and duplicate moved
    assert f_orig.exists()
    assert not f_dupe.exists()
    quarantined = list(quarantine_dir.glob("*.txt"))
    assert len(quarantined) == 1
    assert quarantined[0].name == "duplicate.txt"
    assert quarantined[0].read_text() == "identical content for testing quarantine"

    # 5. Verify batch is recorded in history journal and undo restores file
    last_batch_id = gui_api.history.last_batch_id()
    assert last_batch_id is not None

    undo_results = gui_api.undo(batch_id=last_batch_id)
    assert len(undo_results) == 1
    assert undo_results[0].ok is True
    assert undo_results[0].restored_to == f_dupe

    # Verify duplicate is restored to original path and quarantine is empty
    assert f_dupe.exists()
    assert f_dupe.read_text() == "identical content for testing quarantine"
    assert not quarantined[0].exists()

    # 6. Verify non-exact duplicate groups CANNOT be quarantined
    info_a = FileInfo.from_path(f_orig)
    info_b = FileInfo.from_path(f_dupe)
    potential_group = DuplicateGroup(
        kind="potential",
        files=(info_a, info_b),
        wasted_bytes=info_a.size,
    )
    view.set_groups([potential_group])

    warning_called = False

    def mock_warning(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        nonlocal warning_called
        warning_called = True
        return QMessageBox.StandardButton.Ok

    with patch.object(QMessageBox, "warning", side_effect=mock_warning):
        view.quarantine_btn.click()
        assert warning_called
        # Verify no files were moved
        assert f_orig.exists()
        assert f_dupe.exists()
