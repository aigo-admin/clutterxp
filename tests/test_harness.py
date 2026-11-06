"""Tests proving that test harness fixtures and fakes work correctly."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from clutterxp.config.schema import Config
from clutterxp.interfaces import History, Mover, RuleEngine
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    Decision,
    DecisionKind,
    ErrorCode,
    FileInfo,
    MoveResult,
    MoveStrategy,
    OpStatus,
    RuleHit,
)
from tests.conftest import (
    AssertUnchangedFn,
    ConfigTextFn,
    EnvPaths,
    MakeTreeFn,
    SnapshotFn,
)
from tests.helpers import (
    FakeClock,
    FakeHistory,
    FakeMover,
    FakeRuleEngine,
    wait_until,
)


def test_fake_mover_protocol() -> None:
    """FakeMover must implement Mover Protocol (runtime_checkable)."""
    fake = FakeMover()
    assert isinstance(fake, Mover)


def test_fake_history_protocol() -> None:
    """FakeHistory must implement History Protocol (runtime_checkable)."""
    fake = FakeHistory()
    assert isinstance(fake, History)


def test_fake_rule_engine_protocol() -> None:
    """FakeRuleEngine must implement RuleEngine Protocol (runtime_checkable)."""
    fake = FakeRuleEngine()
    assert isinstance(fake, RuleEngine)


def test_env_fixture(env: EnvPaths) -> None:
    """env fixture must set CLUTTERXP environment variables into tmp_path."""
    assert os.environ["CLUTTERXP_CONFIG"] == str(env.config_path)
    assert os.environ["CLUTTERXP_STATE_DIR"] == str(env.state_dir)
    assert os.environ["CLUTTERXP_CACHE_DIR"] == str(env.cache_dir)
    assert env.state_dir.is_dir()
    assert env.cache_dir.is_dir()


def test_make_tree_fixture(
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """make_tree must create requested file hierarchy with correct contents."""
    tree = make_tree(
        {
            "inbox/a.pdf": b"binary content",
            "inbox/.hidden": b"",
            "nested/sub/file.txt": "text content",
        }
    )
    assert tree == tmp_path
    assert (tree / "inbox/a.pdf").read_bytes() == b"binary content"
    assert (tree / "inbox/.hidden").read_bytes() == b""
    assert (tree / "nested/sub/file.txt").read_text(encoding="utf-8") == "text content"


def test_snapshot_fixture(
    make_tree: MakeTreeFn,
    snapshot: SnapshotFn,
) -> None:
    """snapshot must capture file size, mtime_ns, and sha256 digest."""
    content = b"snapshot test content"
    tree = make_tree({"docs/test.txt": content})

    snap = snapshot(tree)
    assert "docs/test.txt" in snap
    size, mtime_ns, sha256_digest = snap["docs/test.txt"]

    assert size == len(content)
    assert isinstance(mtime_ns, int) and mtime_ns > 0
    assert sha256_digest == hashlib.sha256(content).hexdigest()


def test_assert_unchanged_raises_when_file_changes(
    make_tree: MakeTreeFn,
    snapshot: SnapshotFn,
    assert_unchanged: AssertUnchangedFn,
) -> None:
    """assert_unchanged must pass when unchanged and raise AssertionError when modified."""
    tree = make_tree({"sample.txt": b"initial content"})
    before = snapshot(tree)

    # Initial check passes
    assert_unchanged(tree, before)

    # Modify file content
    time.sleep(0.01)
    (tree / "sample.txt").write_bytes(b"modified content")
    with pytest.raises(AssertionError, match="Tree changed"):
        assert_unchanged(tree, before)

    # Add new file
    with pytest.raises(AssertionError, match="Tree changed"):
        (tree / "added.txt").write_bytes(b"new")
        assert_unchanged(tree, before)


def test_second_fs_device(tmp_path: Path, second_fs: Path) -> None:
    """second_fs must be on a different st_dev than tmp_path."""
    tmp_dev = tmp_path.stat().st_dev
    second_dev = second_fs.stat().st_dev
    assert second_dev != tmp_dev
    assert second_fs.is_dir()


def test_fake_clock_injectable(fake_clock: FakeClock) -> None:
    """fake_clock must provide controllable monotonic time."""
    assert fake_clock() == 0.0
    assert fake_clock.time() == 0.0

    advanced = fake_clock.advance(5.0)
    assert advanced == 5.0
    assert fake_clock() == 5.0

    fake_clock.set(20.0)
    assert fake_clock.time() == 20.0

    with pytest.raises(ValueError, match="Cannot advance clock backwards"):
        fake_clock.advance(-1.0)

    with pytest.raises(ValueError, match="Cannot set clock backwards"):
        fake_clock.set(10.0)


def test_config_text_valid_yaml(config_text: ConfigTextFn) -> None:
    """config_text must produce valid YAML parseable by pydantic schema."""
    yaml_output = config_text(
        rules=[
            {
                "name": "Receipts",
                "keywords": ["receipt", "bill"],
                "destination": "~/Documents/Receipts",
                "collision": "rename",
            }
        ],
        settings={"collision": "rename", "confirm_threshold": 5},
        watch=[{"path": "~/Downloads", "recursive": False}],
    )

    yaml = YAML()
    parsed_data = yaml.load(yaml_output)
    cfg = Config.model_validate(parsed_data)

    assert cfg.version == 1
    assert len(cfg.rules) == 1
    assert cfg.rules[0].name == "Receipts"
    assert cfg.rules[0].destination == "~/Documents/Receipts"
    assert cfg.rules[0].collision == CollisionPolicy.RENAME
    assert cfg.settings.confirm_threshold == 5
    assert len(cfg.watch) == 1
    assert cfg.watch[0].path == "~/Downloads"


def test_wait_until() -> None:
    """wait_until must poll predicate and raise TimeoutError on expiry."""
    counter = [0]

    def inc() -> bool:
        counter[0] += 1
        return counter[0] >= 3

    wait_until(inc, timeout=1.0, interval=0.01)
    assert counter[0] >= 3

    with pytest.raises(TimeoutError, match="timed out"):
        wait_until(lambda: False, timeout=0.05, interval=0.01)


def test_fake_mover_records_and_fails(tmp_path: Path) -> None:
    """FakeMover must record calls and support configured failures."""
    mover = FakeMover()
    src = tmp_path / "source.txt"
    dest_dir = tmp_path / "dest"
    src.write_bytes(b"data")

    res = mover.move_file(src, dest_dir, new_name="target.txt")
    assert res.ok is True
    assert res.src == src
    assert res.dst == dest_dir / "target.txt"
    assert len(mover.calls) == 1
    assert mover.calls[0].new_name == "target.txt"

    failing = FakeMover(failure_code=ErrorCode.PERMISSION)
    fail_res = failing.move_file(src, dest_dir)
    assert fail_res.ok is False
    assert fail_res.code == ErrorCode.PERMISSION


def test_fake_history_journal_first(tmp_path: Path) -> None:
    """FakeHistory must record pending op before returning (S5 journal-first)."""
    hist = FakeHistory()
    b_id = hist.begin_batch("organize", folder=tmp_path)
    assert b_id == 1

    file_path = tmp_path / "file.txt"
    file_path.write_bytes(b"content")
    info = FileInfo.from_path(file_path)

    # Begin op writes pending op immediately
    op_id = hist.begin_op(b_id, file_path, "TestRule", info)
    assert op_id == 1

    pending = hist.pending_ops()
    assert len(pending) == 1
    assert pending[0].id == op_id
    assert pending[0].status == OpStatus.PENDING

    # Finish op updates status to DONE
    dest = tmp_path / "out" / "file.txt"
    res = MoveResult(
        ok=True,
        src=file_path,
        dst=dest,
        strategy=MoveStrategy.RENAME_NOREPLACE,
    )
    hist.finish_op(op_id, res)
    assert len(hist.pending_ops()) == 0

    record = hist.get_op(op_id)
    assert record is not None
    assert record.status == OpStatus.DONE
    assert record.dst_path == dest

    # Mark undone
    hist.mark_undone(op_id)
    undone = hist.get_op(op_id)
    assert undone is not None
    assert undone.status == OpStatus.UNDONE
    assert undone.undone_at is not None


def test_fake_rule_engine(tmp_path: Path) -> None:
    """FakeRuleEngine must return mapped decisions and fallback to NO_MATCH."""
    file_path = tmp_path / "matched.txt"
    file_path.write_bytes(b"abc")
    info = FileInfo.from_path(file_path)

    hit = RuleHit(
        rule_name="TextFiles",
        priority=10,
        dest_dir=tmp_path / "txt",
        static_root=tmp_path / "txt",
        matched_by="ext",
        collision=CollisionPolicy.SKIP,
        create=CreateDestinations.LEAF,
    )
    expected_dec = Decision(file=info, kind=DecisionKind.MATCH, hit=hit)

    engine = FakeRuleEngine(decisions={"matched.txt": expected_dec})
    dec = engine.decide(info)
    assert dec.kind == DecisionKind.MATCH
    assert dec.hit is not None
    assert dec.hit.rule_name == "TextFiles"

    unmatched_path = tmp_path / "other.pdf"
    unmatched_path.write_bytes(b"xyz")
    unmatched_info = FileInfo.from_path(unmatched_path)
    fallback_dec = engine.decide(unmatched_info)
    assert fallback_dec.kind == DecisionKind.NO_MATCH
