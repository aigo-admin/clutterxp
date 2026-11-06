"""Unit tests for Qt table models and formatting utilities."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt

from clutterxp.config.schema import RuleSpec
from clutterxp.gui.models_qt import (
    DuplicatesTableModel,
    HistoryTableModel,
    PlanTableModel,
    RulesTableModel,
    format_bytes,
)
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    Decision,
    DecisionKind,
    DuplicateGroup,
    ErrorCode,
    FileInfo,
    MoveStrategy,
    OperationRecord,
    OpStatus,
    Plan,
    PlannedAction,
    PlanStatus,
    RuleHit,
)


def test_format_bytes() -> None:
    assert format_bytes(500) == "500 B"
    assert format_bytes(2048) == "2.0 KiB"
    assert format_bytes(5 * 1024 * 1024) == "5.0 MiB"
    assert format_bytes(10 * 1024 * 1024 * 1024) == "10.0 GiB"
    assert format_bytes(1024**4) == "1.0 TiB"
    assert format_bytes(1024**5) == "1024.0 PiB"


def test_plan_table_model(tmp_path: Path) -> None:
    # Build sample actions
    f1 = FileInfo(
        path=tmp_path / "a.pdf",
        name="a.pdf",
        stem="a",
        ext="pdf",
        size=100,
        mtime_ns=1000,
        inode=1,
        device=1,
    )
    hit1 = RuleHit(
        rule_name="Invoices",
        priority=10,
        dest_dir=tmp_path / "Invoices",
        static_root=tmp_path / "Invoices",
        matched_by="ext 'pdf'",
        collision=CollisionPolicy.RENAME,
        create=CreateDestinations.ALL,
    )
    act1 = PlannedAction(
        file=f1,
        decision=Decision(file=f1, kind=DecisionKind.MATCH, hit=hit1),
        dest_path=tmp_path / "Invoices" / "a.pdf",
        status=PlanStatus.WILL_MOVE,
    )

    f2 = FileInfo(
        path=tmp_path / "b.txt",
        name="b.txt",
        stem="b",
        ext="txt",
        size=200,
        mtime_ns=2000,
        inode=2,
        device=1,
    )
    act2 = PlannedAction(
        file=f2,
        decision=Decision(
            file=f2,
            kind=DecisionKind.AMBIGUOUS,
            candidates=(hit1, hit1),
            reason="multiple rules",
        ),
        status=PlanStatus.AMBIGUOUS,
        code=ErrorCode.NONE,
    )

    f3 = FileInfo(
        path=tmp_path / "c.tmp",
        name="c.tmp",
        stem="c",
        ext="tmp",
        size=300,
        mtime_ns=3000,
        inode=3,
        device=1,
    )
    act3 = PlannedAction(
        file=f3,
        decision=Decision(file=f3, kind=DecisionKind.NO_MATCH, reason="no rule matched"),
        status=PlanStatus.NO_MATCH,
        message="Unmatched file",
    )

    plan = Plan(folders=(tmp_path,), actions=(act1, act2, act3))

    model = PlanTableModel()
    assert model.rowCount() == 0
    assert model.total_actions_count == 0

    model.set_plan(plan)
    assert model.rowCount() == 3
    assert model.columnCount() == 6
    assert model.total_actions_count == 3
    assert model.included_count == 1  # act1 is WILL_MOVE, act2 & act3 not included by default

    # Header data
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Include"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Status"
    assert model.headerData(99, Qt.Orientation.Horizontal) is None
    assert model.headerData(0, Qt.Orientation.Vertical) is None

    # Cell Data & flags
    idx0 = model.index(0, 0)
    assert model.data(idx0, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert model.data(model.index(0, 1)) == PlanStatus.WILL_MOVE.value
    assert model.data(model.index(0, 2)) == "a.pdf"
    assert model.data(model.index(0, 3)) == str(tmp_path / "Invoices" / "a.pdf")
    assert model.data(model.index(0, 4)) == "Invoices"
    assert model.data(model.index(0, 5)) == "ext 'pdf'"
    assert bool(model.flags(idx0) & Qt.ItemFlag.ItemIsUserCheckable)

    idx1_5 = model.index(1, 5)
    assert "Candidates: Invoices, Invoices" in str(model.data(idx1_5))

    idx2_5 = model.index(2, 5)
    assert model.data(idx2_5) == "Unmatched file"

    # Toggle checkbox
    model.setData(idx0, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert model.included_count == 0
    assert len(model.get_included_plan().actions) == 0

    model.toggle_all(True)
    assert model.included_count == 1  # only WILL_MOVE actions count in included plan
    assert len(model.get_included_plan().actions) == 1

    # Filter
    model.set_filter("will_move")
    assert model.rowCount() == 1
    model.set_filter("ambiguous")
    assert model.rowCount() == 1
    model.set_filter("unmatched")
    assert model.rowCount() == 1
    model.set_filter("blocked")
    assert model.rowCount() == 0
    model.set_filter("all")
    assert model.rowCount() == 3

    assert model.get_action(0) == act1
    assert model.get_action(99) is None

    # Invalid indices
    assert model.data(QModelIndex()) is None
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0


def test_rules_table_model() -> None:
    r1 = RuleSpec(
        name="Docs",
        priority=10,
        keywords=["doc", "file"],
        extensions=["pdf", "docx"],
        destination="/home/test/Docs",
        enabled=True,
    )
    r2 = RuleSpec(
        name="Audio",
        priority=20,
        keywords=["track"],
        extensions=["mp3", "flac"],
        destination="/home/test/Audio",
        enabled=False,
    )

    model = RulesTableModel()
    assert model.rowCount() == 0

    model.set_rules([r1, r2])
    assert model.rowCount() == 2
    assert model.columnCount() == 6

    # Headers
    assert model.headerData(2, Qt.Orientation.Horizontal) == "Name"
    assert model.headerData(99, Qt.Orientation.Horizontal) is None

    # Data
    assert model.data(model.index(0, 0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert model.data(model.index(1, 0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked
    assert model.data(model.index(0, 1)) == 10
    assert model.data(model.index(0, 2)) == "Docs"
    assert model.data(model.index(0, 3)) == "doc, file"
    assert model.data(model.index(0, 4)) == "pdf, docx"
    assert model.data(model.index(0, 5)) == "/home/test/Docs"

    toggled: list[tuple[str, bool]] = []
    model.rule_toggled.connect(lambda name, val: toggled.append((name, val)))
    model.setData(model.index(0, 0), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert toggled == [("Docs", False)]

    assert model.get_rule(0) == r1
    assert model.get_rule(99) is None
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0


def test_history_table_model(tmp_path: Path) -> None:
    rec = OperationRecord(
        id=1,
        batch_id=42,
        status=OpStatus.DONE,
        src_path=tmp_path / "src.txt",
        dst_path=tmp_path / "dst.txt",
        rule_name="Docs",
        strategy=MoveStrategy.RENAME_NOREPLACE,
        size=1024,
        mtime_ns=123456,
        inode=1,
        device=1,
        sha256="abc",
        created_dirs=(),
        created_at="2026-11-06T12:00:00Z",
        finished_at="2026-11-06T12:00:01Z",
        undone_at=None,
        error=None,
    )

    model = HistoryTableModel()
    assert model.rowCount() == 0
    model.set_records([rec])
    assert model.rowCount() == 1
    assert model.columnCount() == 8

    assert model.headerData(0, Qt.Orientation.Horizontal) == "ID"
    assert model.headerData(99, Qt.Orientation.Horizontal) is None

    assert model.data(model.index(0, 0)) == 1
    assert model.data(model.index(0, 1)) == 42
    assert model.data(model.index(0, 2)) == OpStatus.DONE.value
    assert model.data(model.index(0, 3)) == str(tmp_path / "src.txt")
    assert model.data(model.index(0, 4)) == str(tmp_path / "dst.txt")
    assert model.data(model.index(0, 5)) == "Docs"
    assert model.data(model.index(0, 6)) == "2026-11-06T12:00:00Z"
    assert model.data(model.index(0, 7)) == ""

    assert model.get_record(0) == rec
    assert model.get_record(99) is None
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0


def test_duplicates_table_model(tmp_path: Path) -> None:
    f1 = FileInfo(
        path=tmp_path / "f1.txt",
        name="f1.txt",
        stem="f1",
        ext="txt",
        size=100,
        mtime_ns=1,
        inode=1,
        device=1,
    )
    f2 = FileInfo(
        path=tmp_path / "f2.txt",
        name="f2.txt",
        stem="f2",
        ext="txt",
        size=100,
        mtime_ns=2,
        inode=2,
        device=1,
    )
    f3 = FileInfo(
        path=tmp_path / "f3.txt",
        name="f3.txt",
        stem="f3",
        ext="txt",
        size=100,
        mtime_ns=3,
        inode=3,
        device=1,
    )
    f4 = FileInfo(
        path=tmp_path / "f4.txt",
        name="f4.txt",
        stem="f4",
        ext="txt",
        size=100,
        mtime_ns=4,
        inode=4,
        device=1,
    )

    g_exact = DuplicateGroup(
        kind="exact",
        files=(f1, f2, f3, f4),
        sha256="1234567890abcdef1234567890abcdef",
        wasted_bytes=300,
    )
    g_potential = DuplicateGroup(
        kind="potential",
        files=(f1, f2),
        note="similar name and size",
    )

    model = DuplicatesTableModel()
    assert model.rowCount() == 0

    model.set_groups([g_exact, g_potential])
    assert model.rowCount() == 2
    assert model.columnCount() == 5

    assert model.headerData(0, Qt.Orientation.Horizontal) == "Kind"
    assert model.headerData(99, Qt.Orientation.Horizontal) is None

    # Check exact group data
    assert model.data(model.index(0, 0)) == "exact"
    assert model.data(model.index(0, 1)) == 4
    assert model.data(model.index(0, 2)) == "300 B"
    assert "..." in str(model.data(model.index(0, 3)))
    assert "(+1 more)" in str(model.data(model.index(0, 4)))

    # Check potential group data
    assert model.data(model.index(1, 0)) == "potential"
    assert model.data(model.index(1, 3)) == "similar name and size"

    # Filter
    model.set_filter("exact")
    assert model.rowCount() == 1
    model.set_filter("potential")
    assert model.rowCount() == 1
    model.set_filter("hardlink")
    assert model.rowCount() == 0
    model.set_filter("all")
    assert model.rowCount() == 2

    assert model.get_group(0) == g_exact
    assert model.get_group(99) is None
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0
