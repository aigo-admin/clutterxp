"""Tests for planner module."""

from __future__ import annotations

import os
from pathlib import Path

from clutterxp.config.runtime import RuntimeConfig
from clutterxp.config.schema import RuleSpec, Settings, WatchEntry
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    ErrorCode,
    FileInfo,
    PlanStatus,
)
from clutterxp.planner import build_plan
from clutterxp.rules import build_engine


def test_planner_classification_matrix(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    # Create test files
    f_match = src_dir / "invoice.pdf"
    f_match.write_text("invoice")
    f_nomatch = src_dir / "random.xyz"
    f_nomatch.write_text("random")
    f_sub = src_dir / "subdir"
    f_sub.mkdir()

    rules = [
        RuleSpec(
            name="Invoices",
            destination=str(dest_dir),
            keywords=["invoice"],
            priority=10,
        ),
    ]
    engine = build_engine(rules, Settings())
    infos = [
        FileInfo.from_path(f_match),
        FileInfo.from_path(f_nomatch),
        FileInfo.from_path(f_sub),
    ]

    plan = build_plan(infos, engine)
    status_by_name = {a.file.name: a.status for a in plan.actions}

    assert status_by_name["invoice.pdf"] == PlanStatus.WILL_MOVE
    assert status_by_name["random.xyz"] == PlanStatus.NO_MATCH
    assert status_by_name["subdir"] == PlanStatus.IGNORED


def test_planner_ambiguous_decision(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    src_dir.mkdir()
    dest1.mkdir()
    dest2.mkdir()

    f = src_dir / "report_financial.pdf"
    f.write_text("report")

    # Two rules with same priority (10) matching same file with different destinations
    rules = [
        RuleSpec(name="Reports", destination=str(dest1), keywords=["report"], priority=10),
        RuleSpec(name="Financial", destination=str(dest2), keywords=["financial"], priority=10),
    ]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(f)], engine)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.AMBIGUOUS
    assert action.dest_path is None
    assert "Reports" in action.message
    assert "Financial" in action.message


def test_planner_collision_skip(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    src_file = src_dir / "doc.pdf"
    src_file.write_text("new doc")

    # Destination already exists
    existing_dest = dest_dir / "doc.pdf"
    existing_dest.write_text("old doc")

    rules = [
        RuleSpec(
            name="Docs",
            destination=str(dest_dir),
            extensions=["pdf"],
            collision=CollisionPolicy.SKIP,
        )
    ]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(src_file)], engine)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.BLOCKED
    assert action.code == ErrorCode.COLLISION
    assert action.dest_path == existing_dest


def test_planner_collision_rename(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    src_file = src_dir / "data.tar.gz"
    src_file.write_bytes(b"data")

    # Existing destination and first collision candidate
    (dest_dir / "data.tar.gz").write_bytes(b"existing")
    (dest_dir / "data (1).tar.gz").write_bytes(b"existing 1")

    rules = [
        RuleSpec(
            name="Archives",
            destination=str(dest_dir),
            extensions=["gz"],
            collision=CollisionPolicy.RENAME,
        )
    ]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(src_file)], engine)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.WILL_MOVE_RENAMED
    assert action.dest_path == dest_dir / "data (2).tar.gz"


def test_planner_create_destinations_never(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    missing_dest = tmp_path / "missing_dir"
    src_dir.mkdir()

    f = src_dir / "doc.pdf"
    f.write_text("content")

    rules = [
        RuleSpec(
            name="Docs",
            destination=str(missing_dest),
            extensions=["pdf"],
            create_destinations=CreateDestinations.NEVER,
        )
    ]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(f)], engine)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.BLOCKED
    assert action.code == ErrorCode.DEST_MISSING


def test_planner_create_destinations_leaf(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "doc.pdf"
    f.write_text("content")

    # Case 1: static_root parent exists, static_root does not -> allowed
    allowed_leaf = tmp_path / "new_leaf"
    rule_ok = RuleSpec(
        name="DocsOk",
        destination=str(allowed_leaf),
        extensions=["pdf"],
        create_destinations=CreateDestinations.LEAF,
    )
    engine_ok = build_engine([rule_ok], Settings())
    plan_ok = build_plan([FileInfo.from_path(f)], engine_ok)
    assert plan_ok.actions[0].status == PlanStatus.WILL_MOVE

    # Case 2: static_root parent does NOT exist -> BLOCKED: DEST_MISSING
    blocked_leaf = tmp_path / "nonexistent_parent" / "deep_leaf"
    rule_blocked = RuleSpec(
        name="DocsBlocked",
        destination=str(blocked_leaf),
        extensions=["pdf"],
        create_destinations=CreateDestinations.LEAF,
    )
    engine_blocked = build_engine([rule_blocked], Settings())
    plan_blocked = build_plan([FileInfo.from_path(f)], engine_blocked)
    assert plan_blocked.actions[0].status == PlanStatus.BLOCKED
    assert plan_blocked.actions[0].code == ErrorCode.DEST_MISSING


def test_planner_dest_not_writable(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "unwritable_dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "test.txt"
    f.write_text("content")

    # Make destination directory read-only
    dest_dir.chmod(0o555)

    try:
        rules = [RuleSpec(name="Texts", destination=str(dest_dir), extensions=["txt"])]
        engine = build_engine(rules, Settings())
        plan = build_plan([FileInfo.from_path(f)], engine)

        assert len(plan.actions) == 1
        action = plan.actions[0]
        # On systems where root is not running, 0o555 prevents write
        if not os.access(dest_dir, os.W_OK):
            assert action.status == PlanStatus.BLOCKED
            assert action.code == ErrorCode.DEST_NOT_WRITABLE
    finally:
        dest_dir.chmod(0o755)


def test_planner_safety_denylist_destination(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "test.txt"
    f.write_text("content")

    # Destination is /etc/forbidden
    rules = [RuleSpec(name="Forbidden", destination="/etc/forbidden", extensions=["txt"])]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(f)], engine)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.BLOCKED
    assert action.code == ErrorCode.DEST_INVALID


def test_planner_allow_chain_guard(tmp_path: Path) -> None:
    watch_folder = tmp_path / "watched"
    dest_inside = watch_folder / "sub_dest"
    watch_folder.mkdir()
    dest_inside.mkdir()

    f = watch_folder / "file.txt"
    f.write_text("text")

    rules = [RuleSpec(name="Loop", destination=str(dest_inside), extensions=["txt"])]
    settings = Settings(allow_chain=False)
    engine = build_engine(rules, settings)

    config = RuntimeConfig(
        config_path=tmp_path / "config.yaml",
        mtime_ns=0,
        settings=settings,
        watch_entries=(WatchEntry(path=str(watch_folder)),),
        rules=tuple(rules),
        disabled_rules=(),
        errors=(),
        warnings=(),
    )

    plan = build_plan([FileInfo.from_path(f)], engine, config=config)
    assert len(plan.actions) == 1
    assert plan.actions[0].status == PlanStatus.BLOCKED
    assert plan.actions[0].code == ErrorCode.DEST_INVALID
    assert "allow_chain" in plan.actions[0].message


def test_S6_planner_zero_writes(tmp_path: Path, snapshot, assert_unchanged) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    (src_dir / "f1.pdf").write_text("one")
    (src_dir / "f2.txt").write_text("two")
    (dest_dir / "existing.pdf").write_text("three")

    before_snap = snapshot(tmp_path)

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    engine = build_engine(rules, Settings())
    infos = [FileInfo.from_path(src_dir / "f1.pdf"), FileInfo.from_path(src_dir / "f2.txt")]

    plan = build_plan(infos, engine)
    assert plan.count(PlanStatus.WILL_MOVE) == 1

    # Invariant S6: build_plan performs ZERO writes
    assert_unchanged(tmp_path, before_snap)


def test_S7_planner_never_reads_file_contents(tmp_path: Path, monkeypatch) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "sample.pdf"
    f.write_text("secret user data")

    # Monkeypatch open to blow up if called in read mode on user file
    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        if self == f:
            raise AssertionError(f"Safety Invariant S7 violated: read file content of {self}")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(f)], engine)

    assert plan.actions[0].status == PlanStatus.WILL_MOVE


def test_planner_fs_check_false(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    missing_dest = tmp_path / "does_not_exist"
    src_dir.mkdir()
    f = src_dir / "sample.pdf"
    f.write_text("data")

    rules = [
        RuleSpec(
            name="Pdfs",
            destination=str(missing_dest),
            extensions=["pdf"],
            create_destinations=CreateDestinations.NEVER,
        )
    ]
    engine = build_engine(rules, Settings())
    # fs_check = False bypasses filesystem existence / writability checks
    plan = build_plan([FileInfo.from_path(f)], engine, fs_check=False)
    assert plan.actions[0].status == PlanStatus.WILL_MOVE
    assert plan.actions[0].dest_path == missing_dest / "sample.pdf"


def test_planner_source_own_dest(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "already_here.pdf"
    f.write_text("data")

    rules = [RuleSpec(name="Self", destination=str(src_dir), extensions=["pdf"])]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(f)], engine)

    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.BLOCKED
    assert action.code == ErrorCode.DEST_INVALID
    assert "source's own directory" in action.message


def test_planner_exhausted_rename_candidates(tmp_path: Path, monkeypatch) -> None:
    from collections.abc import Generator

    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "photo.jpg"
    f.write_text("photo")
    (dest_dir / "photo.jpg").write_text("original")
    (dest_dir / "photo (1).jpg").write_text("c1")

    # Mock candidate_names to only yield 1 candidate which already exists
    def mock_candidates(name: str) -> Generator[str, None, None]:
        yield "photo (1).jpg"

    monkeypatch.setattr("clutterxp.planner.candidate_names", mock_candidates)

    rules = [
        RuleSpec(
            name="Photos",
            destination=str(dest_dir),
            extensions=["jpg"],
            collision=CollisionPolicy.RENAME,
        )
    ]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(f)], engine)

    assert plan.actions[0].status == PlanStatus.BLOCKED
    assert plan.actions[0].code == ErrorCode.COLLISION
    assert "exhausted" in plan.actions[0].message


def test_planner_create_destinations_all(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "doc.pdf"
    f.write_text("doc")

    deep_dest = tmp_path / "nested" / "deeply" / "destination"
    rules = [
        RuleSpec(
            name="Docs",
            destination=str(deep_dest),
            extensions=["pdf"],
            create_destinations=CreateDestinations.ALL,
        )
    ]
    engine = build_engine(rules, Settings())
    plan = build_plan([FileInfo.from_path(f)], engine)

    assert plan.actions[0].status == PlanStatus.WILL_MOVE
