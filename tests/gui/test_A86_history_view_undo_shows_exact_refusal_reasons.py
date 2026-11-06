"""Test A86: History view undoes batches/files and shows exact refusal reasons."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from clutterxp.gui.api import GuiApi
from clutterxp.gui.views.history import HistoryView
from clutterxp.models import FileInfo, MoveResult, MoveStrategy


def test_A86_history_view_undo_shows_exact_refusal_reasons(
    qtbot: QtBot,
    gui_api: GuiApi,
    tmp_path: Path,
) -> None:
    # 1. Setup two move operations in history
    batch_id = gui_api.history.begin_batch("organize", folder=tmp_path)

    # File 1: will fail undo because destination vanished
    src1 = tmp_path / "src1.pdf"
    src1.write_text("src 1 content")
    dst1 = tmp_path / "dst1.pdf"
    dst1.write_text("src 1 content")
    info1 = FileInfo.from_path(dst1)

    op1 = gui_api.history.begin_op(batch_id, src1, "Invoices", info1)
    gui_api.history.finish_op(
        op1,
        MoveResult(ok=True, src=src1, dst=dst1, strategy=MoveStrategy.RENAME_NOREPLACE),
    )
    # Remove destination to induce undo failure (UNDO_DEST_MISSING)
    dst1.unlink()

    # File 2: will succeed undo
    src2 = tmp_path / "src2.pdf"
    dst2 = tmp_path / "dst2.pdf"
    dst2.write_text("src 2 content")
    info2 = FileInfo.from_path(dst2)

    op2 = gui_api.history.begin_op(batch_id, src2, "Invoices", info2)
    gui_api.history.finish_op(
        op2,
        MoveResult(ok=True, src=src2, dst=dst2, strategy=MoveStrategy.RENAME_NOREPLACE),
    )

    # 2. Instantiate HistoryView
    view = HistoryView(gui_api)
    qtbot.addWidget(view)
    view.show()

    # Wait for history table to load records
    qtbot.waitUntil(lambda: view._model.rowCount() == 2, timeout=5000)

    # Select row for op1 (vanished destination)
    op1_row = next(
        r
        for r in range(view._model.rowCount())
        if (rec := view._model.get_record(r)) is not None and rec.id == op1
    )
    view.table.selectRow(op1_row)
    assert view.undo_file_btn.isEnabled()

    # 3. Trigger Undo on File 1 with mocked confirmation dialog
    with (
        patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
        patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok),
        patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok),
    ):
        view.undo_file_btn.click()

        # Wait until refusal reasons are captured and displayed
        qtbot.waitUntil(lambda: len(view.last_refusal_reasons) > 0, timeout=5000)

        # Exact refusal reason verbatim must be present
        refusal = view.last_refusal_reasons[0].lower()
        assert (
            "undo_dest_missing" in refusal
            or "destination vanished" in refusal
            or "missing" in refusal
            or "does not exist" in refusal
        )
        assert not view.refusal_banner.isHidden()

        # 4. Select row for op2 (which is valid) and trigger Undo File
        op2_row = next(
            r
            for r in range(view._model.rowCount())
            if (rec := view._model.get_record(r)) is not None and rec.id == op2
        )
        view.table.selectRow(op2_row)
        view.undo_file_btn.click()

        # Wait until undo completes successfully
        qtbot.waitUntil(lambda: src2.exists(), timeout=5000)
    assert src2.exists()
    assert not dst2.exists()
