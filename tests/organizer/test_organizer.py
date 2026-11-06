"""Tests for organizer facade conforming to docs/15 acceptance criteria and safety invariants."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clutterxp.config.loader import load
from clutterxp.config.schema import RuleSpec, Settings, WatchEntry
from clutterxp.history.service import HistoryService
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    ErrorCode,
    MoveResult,
    MoveStrategy,
    OpStatus,
    PlanStatus,
    SafetyRefusal,
    UndoOutcome,
)
from clutterxp.mover.safe_move import SafeMover
from clutterxp.organizer import OrganizerImpl, needs_confirmation
from clutterxp.rules import build_engine

_OPEN_SERVICES: list[HistoryService] = []


@pytest.fixture(autouse=True)
def _cleanup_services():
    yield
    for svc in _OPEN_SERVICES:
        svc.close()
    _OPEN_SERVICES.clear()


def _setup_organizer(
    tmp_path: Path,
    rules: list[RuleSpec],
    settings: Settings | None = None,
    watch_entries: list[WatchEntry] | None = None,
    undo_service: Any = None,
) -> tuple[OrganizerImpl, HistoryService, SafeMover, Path]:
    eff_settings = settings or Settings()
    eff_watch = watch_entries or []
    config_file = tmp_path / "config.yaml"

    # Write a basic config file for reload_config support
    config_file.write_text(
        "version: 1\nsettings:\n  collision: skip\nrules:\n  - name: Default\n    destination: "
        + str(tmp_path / "default_dest")
        + "\n    extensions: [pdf]\n"
    )

    runtime_cfg = load(config_file)
    # Override with our specific test configuration
    object.__setattr__(runtime_cfg, "rules", tuple(rules))
    object.__setattr__(runtime_cfg, "settings", eff_settings)
    object.__setattr__(runtime_cfg, "watch_entries", tuple(eff_watch))

    engine = build_engine(rules, eff_settings, watch=eff_watch)
    db_path = tmp_path / "history.db"
    history = HistoryService(db_path)
    _OPEN_SERVICES.append(history)
    mover = SafeMover()
    lock_path = tmp_path / "fs.lock"

    organizer = OrganizerImpl(
        config=runtime_cfg,
        engine=engine,
        mover=mover,
        history=history,
        undo_service=undo_service,
        fs_lock_path=lock_path,
    )
    return organizer, history, mover, db_path


def test_A16_scan_classifies_and_writes_nothing(tmp_path: Path, snapshot, assert_unchanged) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    # Create files for each classification
    f_match = src_dir / "invoice.pdf"
    f_match.write_text("invoice")
    f_nomatch = src_dir / "random.xyz"
    f_nomatch.write_text("random")
    f_ambig = src_dir / "report_2026.pdf"
    f_ambig.write_text("report")
    f_blocked = src_dir / "blocked.pdf"
    f_blocked.write_text("blocked")

    # Destination for f_blocked already exists (collision: skip)
    (dest_dir / "blocked.pdf").write_text("already here")

    rules = [
        RuleSpec(name="Invoices", destination=str(dest_dir), keywords=["invoice"], priority=10),
        RuleSpec(name="Blocked", destination=str(dest_dir), keywords=["blocked"], priority=10),
        RuleSpec(
            name="ReportsA",
            destination=str(tmp_path / "destA"),
            keywords=["report"],
            priority=20,
        ),
        RuleSpec(
            name="ReportsB",
            destination=str(tmp_path / "destB"),
            keywords=["report"],
            priority=20,
        ),
    ]
    (tmp_path / "destA").mkdir()
    (tmp_path / "destB").mkdir()

    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    before = snapshot(tmp_path)
    plan = organizer.plan()

    status_map = {a.file.name: a.status for a in plan.actions}
    assert status_map["invoice.pdf"] == PlanStatus.WILL_MOVE
    assert status_map["random.xyz"] == PlanStatus.NO_MATCH
    assert status_map["report_2026.pdf"] == PlanStatus.AMBIGUOUS
    assert status_map["blocked.pdf"] == PlanStatus.BLOCKED

    # A16: writes nothing
    assert_unchanged(tmp_path, before)


def test_A17_organize_dry_run_leaves_tree_identical(
    tmp_path: Path, snapshot, assert_unchanged
) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    (src_dir / "doc1.pdf").write_text("doc1")
    (src_dir / "doc2.txt").write_text("doc2")

    rules = [RuleSpec(name="Docs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    before = snapshot(tmp_path)

    # Dry-run: plan without apply
    plan = organizer.plan()
    assert plan.count(PlanStatus.WILL_MOVE) == 1

    assert_unchanged(tmp_path, before)


def test_A18_organize_moves_only_match_files(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f_match = src_dir / "matched.pdf"
    f_match.write_text("content match")
    f_unmatch = src_dir / "unmatched.txt"
    f_unmatch.write_text("content unmatch")

    rules = [RuleSpec(name="PDFs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    res = organizer.apply(plan)

    assert res.moved == 1
    assert res.skipped == 1
    assert not f_match.exists()
    assert (dest_dir / "matched.pdf").exists()
    assert f_unmatch.exists()


def test_A19_unmatched_files_remain_in_place_no_unsorted_dir(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    (src_dir / "unknown.xyz").write_text("xyz")
    (src_dir / "random.foo").write_text("foo")

    rules = [RuleSpec(name="PDFs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    res = organizer.apply(plan)

    assert res.moved == 0
    assert res.skipped == 2
    assert (src_dir / "unknown.xyz").exists()
    assert (src_dir / "random.foo").exists()

    # Invariant: No "Unsorted" or similar directory is created
    assert not (src_dir / "Unsorted").exists()
    assert not (dest_dir / "Unsorted").exists()
    assert not (tmp_path / "Unsorted").exists()


def test_A20_ambiguous_files_never_moved_events_recorded(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    src_dir.mkdir()
    dest1.mkdir()
    dest2.mkdir()

    ambig_file = src_dir / "annual_report.pdf"
    ambig_file.write_text("data")

    rules = [
        RuleSpec(name="Annual", destination=str(dest1), keywords=["annual"], priority=10),
        RuleSpec(name="Report", destination=str(dest2), keywords=["report"], priority=10),
    ]
    organizer, history, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    res = organizer.apply(plan)

    assert res.moved == 0
    assert res.skipped == 1
    assert ambig_file.exists()  # Never moved

    # Event skip_ambiguous recorded
    events = history._get_conn().execute("SELECT kind, path, message FROM events;").fetchall()
    assert len(events) == 1
    assert events[0][0] == "skip_ambiguous"
    assert str(ambig_file) in events[0][1]


def test_A21_collision_skip_blocked_rename_moves(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_skip = tmp_path / "dest_skip"
    dest_rename = tmp_path / "dest_rename"
    src_dir.mkdir()
    dest_skip.mkdir()
    dest_rename.mkdir()

    f_skip = src_dir / "file_skip.pdf"
    f_skip.write_text("skip src")
    (dest_skip / "file_skip.pdf").write_text("skip existing")

    f_rename = src_dir / "file_rename.pdf"
    f_rename.write_text("rename src")
    (dest_rename / "file_rename.pdf").write_text("rename existing")

    rules = [
        RuleSpec(
            name="SkipRule",
            destination=str(dest_skip),
            keywords=["file_skip"],
            collision=CollisionPolicy.SKIP,
        ),
        RuleSpec(
            name="RenameRule",
            destination=str(dest_rename),
            keywords=["file_rename"],
            collision=CollisionPolicy.RENAME,
        ),
    ]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    res = organizer.apply(plan)

    assert res.blocked == 1
    assert res.moved == 1

    # f_skip is blocked, destination intact
    assert f_skip.exists()
    assert (dest_skip / "file_skip.pdf").read_text() == "skip existing"

    # f_rename is renamed to file_rename (1).pdf
    assert not f_rename.exists()
    assert (dest_rename / "file_rename.pdf").read_text() == "rename existing"
    assert (dest_rename / "file_rename (1).pdf").read_text() == "rename src"


def test_A22_race_between_plan_and_apply(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "raced.pdf"
    f.write_text("source file")

    rules = [
        RuleSpec(
            name="Raced",
            destination=str(dest_dir),
            extensions=["pdf"],
            collision=CollisionPolicy.SKIP,
        )
    ]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    # 1. Plan time: destination does not exist
    plan = organizer.plan()
    assert plan.actions[0].status == PlanStatus.WILL_MOVE

    # 2. Race: external process creates destination file before apply
    (dest_dir / "raced.pdf").write_text("external intruder")

    # 3. Apply: no-clobber guarantee holds
    res = organizer.apply(plan)
    assert res.moved == 0
    assert res.blocked == 1

    # Invariant S3: Destination never overwritten
    assert (dest_dir / "raced.pdf").read_text() == "external intruder"
    assert f.exists()
    assert f.read_text() == "source file"


def test_A24_missing_destinations_reported(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "sample.pdf").write_text("sample")

    missing_dest = tmp_path / "does_not_exist"
    rules = [
        RuleSpec(
            name="NeverRule",
            destination=str(missing_dest),
            extensions=["pdf"],
            create_destinations=CreateDestinations.NEVER,
        )
    ]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.BLOCKED
    assert action.code == ErrorCode.DEST_MISSING
    assert "does not exist" in action.message


def test_A25_ad_hoc_paths_and_denylist_refusal(tmp_path: Path) -> None:
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    ad_hoc_dir = tmp_path / "ad_hoc_folder"
    ad_hoc_dir.mkdir()
    (ad_hoc_dir / "test.pdf").write_text("test")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(tmp_path, rules, watch_entries=[])

    # Ad-hoc path works even with empty watch_entries
    plan = organizer.plan([ad_hoc_dir])
    assert len(plan.actions) == 1
    assert plan.actions[0].status == PlanStatus.WILL_MOVE

    # Denylisted path is refused with SafetyRefusal
    with pytest.raises(SafetyRefusal):
        organizer.plan([Path("/etc")])

    # Recursive $HOME is refused with SafetyRefusal
    try:
        home = Path.home()
        with pytest.raises(SafetyRefusal):
            organizer.plan([home], recursive=True)
    except RuntimeError:
        pass


def test_A26_A27_batch_and_pending_op_recorded(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "data.pdf"
    f.write_text("recorded content")

    rules = [RuleSpec(name="Data", destination=str(dest_dir), extensions=["pdf"])]
    organizer, history, mover, _db_path = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    # Prove Invariant S5 / A27: pending row is durable before mover moves file
    original_move = mover.move_file
    pending_row_verified = False

    def spy_move(*args, **kwargs) -> MoveResult:
        nonlocal pending_row_verified
        # Query DB to ensure pending row is committed
        pending = history.pending_ops()
        assert len(pending) == 1
        assert pending[0].status == OpStatus.PENDING
        assert pending[0].src_path == f
        pending_row_verified = True
        return original_move(*args, **kwargs)

    mover.move_file = spy_move  # type: ignore[assignment]

    plan = organizer.plan()
    res = organizer.apply(plan)

    assert pending_row_verified is True
    assert res.moved == 1

    # A26: Every successful move has a batch and operation row with metadata
    ops = history.list_ops(status=OpStatus.DONE)
    assert len(ops) == 1
    op = ops[0]
    assert op.src_path == f
    assert op.dst_path == dest_dir / "data.pdf"
    assert op.rule_name == "Data"
    assert op.size == len("recorded content")
    assert op.mtime_ns is not None
    assert op.strategy in (MoveStrategy.RENAME_NOREPLACE, MoveStrategy.LINK_UNLINK)


def test_S1_file_moves_only_when_match(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f_matched = src_dir / "matched.pdf"
    f_matched.write_text("match")
    f_unmatched = src_dir / "unmatched.txt"
    f_unmatched.write_text("nomatch")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    organizer.apply(plan)

    assert not f_matched.exists()
    assert (dest_dir / "matched.pdf").exists()
    assert f_unmatched.exists()


def test_S2_no_match_ambiguous_ignored_untouched(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    src_dir.mkdir()
    dest1.mkdir()
    dest2.mkdir()

    f_nomatch = src_dir / "file.nomatch"
    f_nomatch.write_text("no match")
    f_ambig = src_dir / "file.ambig"
    f_ambig.write_text("ambig")
    (src_dir / ".hidden_file").write_text("hidden")

    rules = [
        RuleSpec(name="R1", destination=str(dest1), extensions=["ambig"], priority=5),
        RuleSpec(name="R2", destination=str(dest2), extensions=["ambig"], priority=5),
    ]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    organizer.apply(plan)

    assert f_nomatch.exists()
    assert f_ambig.exists()
    assert (src_dir / ".hidden_file").exists()


def test_S6_dry_run_zero_writes(tmp_path: Path, snapshot, assert_unchanged) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    (src_dir / "test.pdf").write_text("test")
    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    before = snapshot(tmp_path)
    _ = organizer.plan()
    assert_unchanged(tmp_path, before)


def test_S7_sorting_never_reads_file_contents(tmp_path: Path, monkeypatch) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "large.pdf"
    f.write_text("never read this!")

    original_open = Path.open

    def guarded_open(self: Path, *args, **kwargs):
        if self == f and (not args or "r" in args[0] or "rb" in args[0]):
            raise AssertionError(f"Safety S7 violated: read file contents of {self}")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    res = organizer.apply(plan)
    assert res.moved == 1
    assert (dest_dir / "large.pdf").exists()


def test_S15_failing_file_does_not_abort_batch(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f1 = src_dir / "file1.pdf"
    f1.write_text("one")
    f2 = src_dir / "file2.pdf"
    f2.write_text("two")
    f3 = src_dir / "file3.pdf"
    f3.write_text("three")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, mover, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()

    # Make file2 fail during move
    original_move = mover.move_file

    def failing_move(src, dest, *args, **kwargs):
        if Path(src).name == "file2.pdf":
            return MoveResult(
                ok=False,
                src=Path(src),
                dst=None,
                strategy=None,
                code=ErrorCode.PERMISSION,
                message="Mock permission error",
            )
        return original_move(src, dest, *args, **kwargs)

    mover.move_file = failing_move  # type: ignore[assignment]

    res = organizer.apply(plan)

    assert res.moved == 2
    assert res.failed == 1
    assert (dest_dir / "file1.pdf").exists()
    assert (dest_dir / "file3.pdf").exists()
    assert f2.exists()  # Failed file left in place


def test_reload_config_valid_and_invalid(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    dest1.mkdir()
    dest2.mkdir()

    config_file.write_text(
        "version: 1\nsettings:\n  collision: skip\nrules:\n"
        f"  - name: RuleV1\n    destination: {dest1}\n    extensions: [pdf]\n"
    )

    runtime_cfg = load(config_file)
    engine = build_engine(list(runtime_cfg.rules), runtime_cfg.settings)
    history = HistoryService(tmp_path / "history.db")
    _OPEN_SERVICES.append(history)
    mover = SafeMover()

    organizer = OrganizerImpl(config=runtime_cfg, engine=engine, mover=mover, history=history)
    assert len(organizer.config.rules) == 1
    assert organizer.config.rules[0].name == "RuleV1"

    # 1. Update config with valid new content
    config_file.write_text(
        "version: 1\nsettings:\n  collision: rename\nrules:\n"
        f"  - name: RuleV2\n    destination: {dest2}\n    extensions: [txt]\n"
    )
    organizer.reload_config()
    assert organizer.config.rules[0].name == "RuleV2"
    assert organizer.config.settings.collision == CollisionPolicy.RENAME

    # 2. Write invalid config (invalid YAML / unknown fields)
    config_file.write_text("version: 1\nsettings:\n  unknown_invalid_key: 123\n")
    organizer.reload_config()
    # Still has RuleV2 active (did not crash, rejected invalid config)
    assert organizer.config.rules[0].name == "RuleV2"


def test_process_file_single_batch(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "incoming.pdf"
    f.write_text("incoming")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _history, _, _ = _setup_organizer(tmp_path, rules)

    res = organizer.process_file(f, folder=src_dir, command="watch")
    assert res.moved == 1
    assert res.batch_id is not None
    assert (dest_dir / "incoming.pdf").exists()

    # Nonexistent file returns failed batch of 1
    res_missing = organizer.process_file(src_dir / "ghost.pdf")
    assert res_missing.failed == 1
    assert res_missing.moved == 0


def test_needs_confirmation(tmp_path: Path) -> None:
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Create 3 files
    for i in range(3):
        (src_dir / f"f{i}.pdf").write_text(str(i))

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]

    # Threshold = 2 -> 3 files >= 2 -> True
    organizer, _, _, _ = _setup_organizer(
        tmp_path,
        rules,
        settings=Settings(confirm_threshold=2),
        watch_entries=[WatchEntry(path=str(src_dir))],
    )
    plan = organizer.plan()
    assert organizer.needs_confirmation(plan) is True

    # Threshold = 5 -> 3 files < 5 -> False
    assert needs_confirmation(plan, threshold=5) is False

    # Threshold = 0 -> always ask when files > 0
    assert needs_confirmation(plan, threshold=0) is True


def test_undo_service_delegation(tmp_path: Path) -> None:
    rules = [RuleSpec(name="R", destination=str(tmp_path / "d"), extensions=["pdf"])]

    class FakeUndoService:
        def __init__(self) -> None:
            self.called = False

        def undo(self, **kwargs) -> list[UndoOutcome]:
            self.called = True
            return [UndoOutcome(op_id=1, ok=True)]

    fake_undo = FakeUndoService()
    organizer, _, _, _ = _setup_organizer(tmp_path, rules, undo_service=fake_undo)

    res = organizer.undo(op_id=1)
    assert fake_undo.called is True
    assert len(res) == 1
    assert res[0].ok is True

    # Without undo service -> NotImplementedError
    organizer_no_undo, _, _, _ = _setup_organizer(tmp_path, rules, undo_service=None)
    with pytest.raises(NotImplementedError):
        organizer_no_undo.undo(op_id=1)


def test_only_rules_filter(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    src_dir.mkdir()
    dest1.mkdir()
    dest2.mkdir()

    f = src_dir / "photo.jpg"
    f.write_text("jpg")

    rules = [
        RuleSpec(name="RuleA", destination=str(dest1), extensions=["jpg"]),
        RuleSpec(name="RuleB", destination=str(dest2), extensions=["jpg"]),
    ]
    organizer, _, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    # Filter with only_rules=["RuleB"]
    plan = organizer.plan(only_rules=["RuleB"])
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.status == PlanStatus.WILL_MOVE
    assert action.decision.hit is not None
    assert action.decision.hit.rule_name == "RuleB"
    assert action.dest_path == dest2 / "photo.jpg"


def test_organizer_plan_single_file(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "single.pdf"
    f.write_text("content")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _, _, _ = _setup_organizer(tmp_path, rules)

    plan = organizer.plan([f])
    assert len(plan.actions) == 1
    assert plan.actions[0].status == PlanStatus.WILL_MOVE
    assert plan.actions[0].dest_path == dest_dir / "single.pdf"


def test_organizer_toctou_source_vanished(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "vanish.pdf"
    f.write_text("vanish")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _history, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    assert plan.actions[0].status == PlanStatus.WILL_MOVE

    # Simulate source vanishing after plan
    f.unlink()

    res = organizer.apply(plan)
    assert res.failed == 1
    assert res.moved == 0
    assert len(res.results) == 1
    assert res.results[0].code == ErrorCode.SRC_VANISHED


def test_organizer_toctou_source_changed(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "modify.pdf"
    f.write_text("original")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    organizer, _history, _, _ = _setup_organizer(
        tmp_path, rules, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    assert plan.actions[0].status == PlanStatus.WILL_MOVE

    # Simulate source modification after plan
    f.write_text("modified content after planning")

    res = organizer.apply(plan)
    assert res.failed == 1
    assert res.moved == 0
    assert len(res.results) == 1
    assert res.results[0].code == ErrorCode.SRC_CHANGED


def test_organizer_log_no_match(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()

    f = src_dir / "nomatch.xyz"
    f.write_text("nomatch")

    rules = [RuleSpec(name="Pdfs", destination=str(dest_dir), extensions=["pdf"])]
    settings = Settings()
    settings.history.log_no_match = True

    organizer, history, _, _ = _setup_organizer(
        tmp_path, rules, settings=settings, watch_entries=[WatchEntry(path=str(src_dir))]
    )

    plan = organizer.plan()
    res = organizer.apply(plan)
    assert res.skipped == 1

    events = (
        history._get_conn()
        .execute("SELECT kind, message FROM events WHERE kind = 'skip_no_match';")
        .fetchall()
    )
    assert len(events) == 1


def test_organizer_default_fs_lock_path(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("version: 1\n")
    runtime_cfg = load(config_file)
    engine = build_engine([], runtime_cfg.settings)
    history = HistoryService(tmp_path / "h.db")
    _OPEN_SERVICES.append(history)
    mover = SafeMover()

    organizer = OrganizerImpl(config=runtime_cfg, engine=engine, mover=mover, history=history)
    # Default lock path from paths module
    assert organizer.fs_lock_path.name == "fs.lock"


def test_organizer_reload_config_exception(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("version: 1\n")
    runtime_cfg = load(config_file)
    engine = build_engine([], runtime_cfg.settings)
    history = HistoryService(tmp_path / "h.db")
    _OPEN_SERVICES.append(history)
    mover = SafeMover()

    organizer = OrganizerImpl(config=runtime_cfg, engine=engine, mover=mover, history=history)

    def failing_load(*args, **kwargs):
        raise OSError("Disk failure")

    monkeypatch.setattr("clutterxp.organizer.loader.load", failing_load)
    # Does not crash on exception
    organizer.reload_config()
