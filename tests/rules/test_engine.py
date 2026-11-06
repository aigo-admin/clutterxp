"""Tests for RuleEngineImpl.

Proves Acceptance Criteria A10, A11, A12, A13, A14, A20,
and Safety Invariants S1, S2, S7.
"""

from __future__ import annotations

from pathlib import Path

from clutterxp.config.schema import Conditions, RuleSpec, Settings, WatchEntry
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    DecisionKind,
    FileInfo,
)
from clutterxp.rules.engine import RuleEngineImpl


def _make_file(
    name: str = "doc.pdf",
    size: int = 1024,
    mtime_ns: int = 1700000000000000000,
    is_regular: bool = True,
    is_symlink: bool = False,
    path: Path | None = None,
) -> FileInfo:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    ext = name.rsplit(".", 1)[1].lower() if "." in name else ""
    return FileInfo(
        path=path or Path(f"/tmp/{name}"),
        name=name,
        stem=stem,
        ext=ext,
        size=size,
        mtime_ns=mtime_ns,
        inode=1,
        device=1,
        is_regular=is_regular,
        is_symlink=is_symlink,
    )


def test_A10_size_and_age_conditions() -> None:
    # A10: min_size, max_size, older_than, newer_than conditions work from lstat data only
    base_mtime_ns = 1700000000000000000
    now_ns = base_mtime_ns + (10 * 86400 * 1_000_000_000)

    # 1. Size conditions (min_size and max_size)
    size_rule = RuleSpec(
        name="SizeRule",
        destination="/dest/size",
        keywords=["invoice"],
        conditions=Conditions(min_size="10KB", max_size="50MB"),
    )
    engine_size = RuleEngineImpl([size_rule], Settings(), now_ns=now_ns)

    file_valid = _make_file(name="invoice_valid.pdf", size=20 * 1024, mtime_ns=base_mtime_ns)
    assert engine_size.decide(file_valid).kind == DecisionKind.MATCH

    file_too_small = _make_file(name="invoice_small.pdf", size=5 * 1024, mtime_ns=base_mtime_ns)
    assert engine_size.decide(file_too_small).kind == DecisionKind.NO_MATCH

    file_too_large = _make_file(
        name="invoice_large.pdf", size=60 * 1024 * 1024, mtime_ns=base_mtime_ns
    )
    assert engine_size.decide(file_too_large).kind == DecisionKind.NO_MATCH

    # 2. Age conditions (older_than and newer_than tested individually)
    older_rule = RuleSpec(
        name="OlderRule",
        destination="/dest/older",
        keywords=["invoice"],
        conditions=Conditions(older_than="2d"),
    )
    engine_older = RuleEngineImpl([older_rule], Settings(), now_ns=now_ns)

    # 10 days old > 2d -> matches
    assert engine_older.decide(file_valid).kind == DecisionKind.MATCH

    # 1 day old <= 2d -> fails
    mtime_too_new = now_ns - (1 * 86400 * 1_000_000_000)
    file_too_new = _make_file(name="invoice_new.pdf", size=20 * 1024, mtime_ns=mtime_too_new)
    assert engine_older.decide(file_too_new).kind == DecisionKind.NO_MATCH

    newer_rule = RuleSpec(
        name="NewerRule",
        destination="/dest/newer",
        keywords=["invoice"],
        conditions=Conditions(newer_than="30d"),
    )
    engine_newer = RuleEngineImpl([newer_rule], Settings(), now_ns=now_ns)

    # 10 days old < 30d -> matches
    assert engine_newer.decide(file_valid).kind == DecisionKind.MATCH

    # 40 days old >= 30d -> fails
    mtime_too_old = now_ns - (40 * 86400 * 1_000_000_000)
    file_too_old = _make_file(name="invoice_old.pdf", size=20 * 1024, mtime_ns=mtime_too_old)
    assert engine_newer.decide(file_too_old).kind == DecisionKind.NO_MATCH


def test_A11_all_of_and_exclude_behavior() -> None:
    # A11: all_of and exclude behave as specified
    rule = RuleSpec(
        name="ProjectReports",
        destination="/dest/projects",
        all_of=["sec", "report"],
        exclude=["draft", "temp"],
    )
    engine = RuleEngineImpl([rule], Settings())

    # Matches when all_of entries present
    f_match = _make_file("sec_final_report.pdf")
    assert engine.decide(f_match).kind == DecisionKind.MATCH

    # Fails when an all_of entry is missing
    f_missing = _make_file("sec_final_notes.pdf")
    assert engine.decide(f_missing).kind == DecisionKind.NO_MATCH

    # Fails when an exclude entry matches
    f_excluded = _make_file("sec_draft_report.pdf")
    assert engine.decide(f_excluded).kind == DecisionKind.NO_MATCH


def test_A12_lower_priority_number_wins() -> None:
    # A12: Lower priority number wins over higher
    rule_high = RuleSpec(
        name="HighPriority",
        priority=10,
        destination="/dest/high",
        keywords=["doc"],
    )
    rule_low = RuleSpec(
        name="LowPriority",
        priority=50,
        destination="/dest/low",
        keywords=["doc"],
    )
    engine = RuleEngineImpl([rule_low, rule_high], Settings())

    file = _make_file("doc_test.pdf")
    decision = engine.decide(file)

    assert decision.kind == DecisionKind.MATCH
    assert decision.hit is not None
    assert decision.hit.rule_name == "HighPriority"
    assert decision.hit.priority == 10
    assert "LowPriority" in decision.hit.also_matched


def test_A13_equal_priority_ambiguous_and_same_dest() -> None:
    # A13: Equal best priority with different destinations is AMBIGUOUS; same destination is not
    # 1. Different destinations -> AMBIGUOUS
    r1 = RuleSpec(name="R1", priority=10, destination="/dest/one", keywords=["doc"])
    r2 = RuleSpec(name="R2", priority=10, destination="/dest/two", keywords=["doc"])
    engine_ambig = RuleEngineImpl([r1, r2], Settings())

    file = _make_file("doc_test.pdf")
    dec_ambig = engine_ambig.decide(file)
    assert dec_ambig.kind == DecisionKind.AMBIGUOUS
    assert len(dec_ambig.candidates) == 2
    assert {c.rule_name for c in dec_ambig.candidates} == {"R1", "R2"}

    # 2. Same destination -> MATCH with first in file order, other in also_matched
    r3 = RuleSpec(name="R3", priority=10, destination="/dest/same", keywords=["doc"])
    r4 = RuleSpec(name="R4", priority=10, destination="/dest/same", keywords=["doc"])
    engine_same = RuleEngineImpl([r3, r4], Settings())

    dec_same = engine_same.decide(file)
    assert dec_same.kind == DecisionKind.MATCH
    assert dec_same.hit is not None
    assert dec_same.hit.rule_name == "R3"
    assert "R4" in dec_same.hit.also_matched


def test_A14_trace_explains_every_rule() -> None:
    # A14: trace output has entry per rule
    r1 = RuleSpec(name="SecLeaf", priority=10, destination="/dest/sl", keywords=["sl"])
    r2 = RuleSpec(name="ThunderCipher", priority=20, destination="/dest/tc", keywords=["tc"])
    r3 = RuleSpec(name="College", priority=30, destination="/dest/col", keywords=["lab"])

    engine = RuleEngineImpl([r1, r2, r3], Settings())
    file = _make_file("SL-CTF-writeup.pdf")

    traces = engine.trace(file)
    assert len(traces) == 3

    assert traces[0].rule_name == "SecLeaf"
    assert traces[0].matched is True
    assert traces[0].priority == 10
    assert any("keyword 'sl' (token)" in r for r in traces[0].reasons)

    assert traces[1].rule_name == "ThunderCipher"
    assert traces[1].matched is False
    assert traces[1].priority == 20
    assert "no keyword matched" in traces[1].reasons

    assert traces[2].rule_name == "College"
    assert traces[2].matched is False
    assert traces[2].priority == 30


def test_A20_ambiguous_result_shape() -> None:
    # A20: Ambiguous files are never moved and are listed with their candidates
    r1 = RuleSpec(name="RuleA", priority=5, destination="/dest/a", keywords=["doc"])
    r2 = RuleSpec(name="RuleB", priority=5, destination="/dest/b", keywords=["doc"])
    engine = RuleEngineImpl([r1, r2], Settings())

    file = _make_file("doc_presentation.pdf")
    decision = engine.decide(file)

    assert decision.kind == DecisionKind.AMBIGUOUS
    assert decision.hit is None  # Never moved!
    assert isinstance(decision.candidates, tuple)
    assert len(decision.candidates) == 2
    assert decision.candidates[0].rule_name == "RuleA"
    assert decision.candidates[1].rule_name == "RuleB"


def test_S1_file_moves_only_when_match() -> None:
    # S1: A file is moved only when a rule explicitly matches and the decision is MATCH
    rule = RuleSpec(name="Invoices", destination="/dest/inv", keywords=["invoice"])
    engine = RuleEngineImpl([rule], Settings())

    f_match = _make_file("invoice_1.pdf")
    dec_match = engine.decide(f_match)
    assert dec_match.kind == DecisionKind.MATCH
    assert dec_match.hit is not None

    f_nomatch = _make_file("photo.jpg")
    dec_nomatch = engine.decide(f_nomatch)
    assert dec_nomatch.kind == DecisionKind.NO_MATCH
    assert dec_nomatch.hit is None


def test_S2_no_match_ambiguous_ignored_never_get_hit() -> None:
    # S2: NO_MATCH, AMBIGUOUS and IGNORED files never get WILL_MOVE / never have a hit
    r_ambig1 = RuleSpec(name="A1", priority=1, destination="/dest/1", keywords=["file"])
    r_ambig2 = RuleSpec(name="A2", priority=1, destination="/dest/2", keywords=["file"])
    engine = RuleEngineImpl([r_ambig1, r_ambig2], Settings(skip_hidden=True, follow_symlinks=False))

    # NO_MATCH
    f_nomatch = _make_file("random.txt")
    d1 = engine.decide(f_nomatch)
    assert d1.kind == DecisionKind.NO_MATCH
    assert d1.hit is None

    # AMBIGUOUS
    f_ambig = _make_file("file.txt")
    d2 = engine.decide(f_ambig)
    assert d2.kind == DecisionKind.AMBIGUOUS
    assert d2.hit is None

    # IGNORED (hidden)
    f_hidden = _make_file(".hidden.txt")
    d3 = engine.decide(f_hidden)
    assert d3.kind == DecisionKind.IGNORED
    assert d3.hit is None

    # IGNORED (symlink)
    f_symlink = _make_file("link.txt", is_symlink=True)
    d4 = engine.decide(f_symlink)
    assert d4.kind == DecisionKind.IGNORED
    assert d4.hit is None

    # IGNORED (non-regular)
    f_dir = _make_file("directory", is_regular=False)
    d5 = engine.decide(f_dir)
    assert d5.kind == DecisionKind.IGNORED
    assert d5.hit is None


def test_S7_hand_built_nonexistent_fileinfo_no_io() -> None:
    # S7: Sorting never reads file contents and never hashes
    # Hand-built FileInfo on non-existent path runs with zero I/O
    rule = RuleSpec(name="Receipts", destination="/dest/receipts", keywords=["receipt"])
    engine = RuleEngineImpl([rule], Settings())

    nonexistent_path = Path("/absolutely/nonexistent/filesystem/path/fake_receipt_123.pdf")
    file = FileInfo(
        path=nonexistent_path,
        name="fake_receipt_123.pdf",
        stem="fake_receipt_123",
        ext="pdf",
        size=4096,
        mtime_ns=1700000000000000000,
        inode=999,
        device=999,
    )
    decision = engine.decide(file)
    assert decision.kind == DecisionKind.MATCH
    assert decision.hit is not None
    assert decision.hit.rule_name == "Receipts"


def test_per_folder_rule_subsets() -> None:
    r1 = RuleSpec(name="DownloadsRule", destination="/dest/dl", keywords=["doc"])
    r2 = RuleSpec(name="DesktopRule", destination="/dest/dt", keywords=["doc"])

    watch_entries = [
        WatchEntry(path="/home/alice/Downloads", rules=["DownloadsRule"]),
        WatchEntry(path="/home/alice/Desktop", rules=["DesktopRule"]),
    ]

    engine = RuleEngineImpl([r1, r2], Settings(), watch=watch_entries)
    file = _make_file("doc.pdf")

    # In Downloads: only DownloadsRule applies
    dec_dl = engine.decide(file, folder=Path("/home/alice/Downloads"))
    assert dec_dl.kind == DecisionKind.MATCH
    assert dec_dl.hit is not None
    assert dec_dl.hit.rule_name == "DownloadsRule"

    # In Desktop: only DesktopRule applies
    dec_dt = engine.decide(file, folder=Path("/home/alice/Desktop"))
    assert dec_dt.kind == DecisionKind.MATCH
    assert dec_dt.hit is not None
    assert dec_dt.hit.rule_name == "DesktopRule"

    # In other folder: all rules apply (equal priority, different dest -> AMBIGUOUS)
    dec_other = engine.decide(file, folder=Path("/home/alice/Documents"))
    assert dec_other.kind == DecisionKind.AMBIGUOUS


def test_override_collision_and_create_destinations() -> None:
    rule = RuleSpec(
        name="Custom",
        destination="/dest/custom",
        keywords=["test"],
        collision=CollisionPolicy.RENAME,
        create_destinations=CreateDestinations.ALL,
    )
    engine = RuleEngineImpl(
        [rule],
        Settings(
            collision=CollisionPolicy.SKIP,
            create_destinations=CreateDestinations.LEAF,
        ),
    )
    file = _make_file("test_file.txt")
    dec = engine.decide(file)
    assert dec.kind == DecisionKind.MATCH
    assert dec.hit is not None
    assert dec.hit.collision == CollisionPolicy.RENAME
    assert dec.hit.create == CreateDestinations.ALL


def test_build_engine_factory() -> None:
    from clutterxp.rules import build_engine

    rule = RuleSpec(name="R", destination="/dest/r", keywords=["test"])
    engine = build_engine([rule], Settings())
    assert isinstance(engine, RuleEngineImpl)


def test_folder_rules_dict_initialization() -> None:
    rule = RuleSpec(name="R", destination="/dest/r", keywords=["test"])
    engine = RuleEngineImpl([rule], Settings(), folder_rules={"/custom/path": ["R"]})
    file = _make_file("test.txt")
    dec = engine.decide(file, folder=Path("/custom/path"))
    assert dec.kind == DecisionKind.MATCH


def test_conditions_only_rule_matched_by() -> None:
    rule = RuleSpec(
        name="BigFiles",
        destination="/dest/big",
        conditions=Conditions(min_size=1000),
    )
    engine = RuleEngineImpl([rule], Settings())
    file = _make_file("anything.dat", size=5000)
    dec = engine.decide(file)
    assert dec.kind == DecisionKind.MATCH
    assert dec.hit is not None
    assert dec.hit.matched_by == "conditions matched"


def test_follow_symlinks_setting() -> None:
    rule = RuleSpec(name="LinkRule", destination="/dest/links", keywords=["link"])
    engine_follow = RuleEngineImpl([rule], Settings(follow_symlinks=True))
    engine_ignore = RuleEngineImpl([rule], Settings(follow_symlinks=False))

    symlink_file = _make_file("link.txt", is_symlink=True)
    assert engine_follow.decide(symlink_file).kind == DecisionKind.MATCH
    assert engine_ignore.decide(symlink_file).kind == DecisionKind.IGNORED


def test_same_dest_multiple_top_with_lower_priority() -> None:
    t1 = RuleSpec(name="T1", priority=10, destination="/dest/same", keywords=["doc"])
    t2 = RuleSpec(name="T2", priority=10, destination="/dest/same", keywords=["doc"])
    low = RuleSpec(name="L1", priority=20, destination="/dest/low", keywords=["doc"])

    engine = RuleEngineImpl([t1, t2, low], Settings())
    file = _make_file("doc.pdf")
    dec = engine.decide(file)
    assert dec.kind == DecisionKind.MATCH
    assert dec.hit is not None
    assert dec.hit.rule_name == "T1"
    assert dec.hit.also_matched == ("T2", "L1")
