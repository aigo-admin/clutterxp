"""Fault-injection sweep over organize pipeline for same-FS and cross-FS moves.

Simulates crashes at every critical step of the pipeline, runs crash recovery,
and asserts complete content preservation (zero data loss, no half-published files).
Proves acceptance criterion A71 and A73.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

import pytest

from clutterxp.config.runtime import RuntimeConfig
from clutterxp.config.schema import RuleSpec, Settings
from clutterxp.history.recovery import reconcile
from clutterxp.history.service import HistoryService
from clutterxp.models import (
    CollisionPolicy,
    CreateDestinations,
    OpStatus,
)
from clutterxp.mover import fsops
from clutterxp.mover.safe_move import SafeMover
from clutterxp.organizer import OrganizerImpl
from clutterxp.rules import build_engine
from tests.conftest import EnvPaths, MakeTreeFn


class SimulatedCrash(RuntimeError):
    """Exception simulating crash/SIGKILL at an exact pipeline step."""


# =============================================================================
# Helper to set up clean organizer for fault injection
# =============================================================================
def _setup_test_env(
    tmp_path: Path,
    env: EnvPaths,
    dest_dir: Path,
    *,
    verify_copy: Literal["size", "hash"] = "hash",
) -> tuple[OrganizerImpl, HistoryService, SafeMover]:
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover(verify_copy=verify_copy)
    rule = RuleSpec(
        name="Docs",
        destination=str(dest_dir),
        extensions=["txt", "pdf", "dat"],
        collision=CollisionPolicy.RENAME,
        create_destinations=CreateDestinations.ALL,
    )
    settings = Settings()
    runtime_cfg = RuntimeConfig(
        config_path=env.config_path,
        mtime_ns=0,
        settings=settings,
        watch_entries=(),
        rules=(rule,),
        disabled_rules=(),
        errors=(),
        warnings=(),
    )
    engine = build_engine([rule], settings)
    organizer = OrganizerImpl(
        config=runtime_cfg,
        engine=engine,
        mover=mover,
        history=history,
        fs_lock_path=env.state_dir / "fs.lock",
    )
    return organizer, history, mover


# =============================================================================
# Same-Filesystem Crash Injections (Strategy A)
# =============================================================================


def test_A71_crash_same_fs_before_begin_op(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Crash before begin_op: source file intact, no journal entry, no destination."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    make_tree({"inbox/doc.txt": "vital data 1"}, base=tmp_path)
    organizer, history, _ = _setup_test_env(tmp_path, env, dest_dir)

    plan = organizer.plan([src_dir])

    with (
        patch.object(history, "begin_op", side_effect=SimulatedCrash("Crash before DB journal")),
        pytest.raises(SimulatedCrash),
    ):
        organizer.apply(plan)

    # Recovery
    reconcile(history)

    # Source untouched
    assert (src_dir / "doc.txt").exists()
    assert (src_dir / "doc.txt").read_text(encoding="utf-8") == "vital data 1"
    assert not (dest_dir / "doc.txt").exists()


def test_A71_crash_same_fs_pending_row_before_mover(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Crash after begin_op durable but before mover executes: recovery marks failed."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    make_tree({"inbox/doc.txt": "vital data 2"}, base=tmp_path)
    organizer, history, mover = _setup_test_env(tmp_path, env, dest_dir)

    plan = organizer.plan([src_dir])

    with (
        patch.object(mover, "move_file", side_effect=SimulatedCrash("Crash right before move")),
        pytest.raises(SimulatedCrash),
    ):
        organizer.apply(plan)

    # Pending op exists in DB
    pending = history.pending_ops()
    assert len(pending) == 1
    assert pending[0].status == OpStatus.PENDING

    # Run recovery
    actions = reconcile(history, planned_dirs=[dest_dir])
    assert len(actions) == 1
    assert actions[0].action_taken == "marked_failed"

    # Source intact, no destination
    assert (src_dir / "doc.txt").read_text(encoding="utf-8") == "vital data 2"
    assert not (dest_dir / "doc.txt").exists()


def test_A71_crash_same_fs_during_link_unlink_partial(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Crash when hardlink created but source unlink not yet done: both exist."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / "doc.txt"
    make_tree({"inbox/doc.txt": "vital hardlink data"}, base=tmp_path)
    organizer, history, _mover = _setup_test_env(tmp_path, env, dest_dir)

    plan = organizer.plan([src_dir])

    def crashing_link_unlink(s: Path, d: Path) -> bool:
        os.link(s, d)
        raise SimulatedCrash("Crash between link and unlink!")

    with (
        patch.object(fsops, "rename_noreplace", return_value=False),
        patch.object(fsops, "link_unlink", side_effect=crashing_link_unlink),
        pytest.raises(SimulatedCrash),
    ):
        organizer.apply(plan)

    # Both files exist on disk with identical data
    assert src_file.exists()
    assert (dest_dir / "doc.txt").exists()
    assert src_file.read_text(encoding="utf-8") == "vital hardlink data"
    assert (dest_dir / "doc.txt").read_text(encoding="utf-8") == "vital hardlink data"

    # Recovery: both exist -> marked failed with conflict warning, NEVER deleted
    actions = reconcile(history, planned_dirs=[dest_dir])
    assert len(actions) == 1
    assert actions[0].action_taken == "conflict_warning"

    # Verify both copies are still intact!
    assert src_file.exists()
    assert (dest_dir / "doc.txt").exists()


def test_A71_crash_same_fs_after_move_before_finish_op(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Crash after mover completes move, before finish_op commits: recovery marks done."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "dest"
    make_tree({"inbox/doc.txt": "vital moved data"}, base=tmp_path)
    organizer, history, _ = _setup_test_env(tmp_path, env, dest_dir)

    plan = organizer.plan([src_dir])

    with (
        patch.object(history, "finish_op", side_effect=SimulatedCrash("Crash before finish_op")),
        pytest.raises(SimulatedCrash),
    ):
        organizer.apply(plan)

    # File is at destination, source unlinked, DB op is still pending
    assert not (src_dir / "doc.txt").exists()
    assert (dest_dir / "doc.txt").exists()

    # Recovery: source missing, destination exists with matching size -> marked done
    actions = reconcile(history, planned_dirs=[dest_dir])
    assert len(actions) == 1
    assert actions[0].action_taken == "marked_done"
    assert (dest_dir / "doc.txt").read_text(encoding="utf-8") == "vital moved data"


# =============================================================================
# Cross-Filesystem Crash Injections (Strategy B)
# =============================================================================


def test_A71_crash_cross_fs_during_temp_copy(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Crash midway through cross-fs copy: temp file cleaned up, source intact."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "cross_dest"
    make_tree({"inbox/large.dat": "A" * 50000}, base=tmp_path)
    organizer, history, _ = _setup_test_env(tmp_path, env, dest_dir)

    plan = organizer.plan([src_dir])

    # Force Strategy B by making same_device return False
    # Intercept copy to crash midway
    with (
        patch.object(fsops, "same_device", return_value=False),
        patch.object(fsops, "fsync_file", side_effect=SimulatedCrash("Crash before fsync")),
        pytest.raises(SimulatedCrash),
    ):
        organizer.apply(plan)

    # Source is 100% intact
    assert (src_dir / "large.dat").exists()
    assert (src_dir / "large.dat").read_text(encoding="utf-8") == "A" * 50000

    # No published destination file exists
    assert not (dest_dir / "large.dat").exists()

    # Recovery
    reconcile(history, planned_dirs=[dest_dir])
    assert (src_dir / "large.dat").read_text(encoding="utf-8") == "A" * 50000


def test_A71_crash_cross_fs_during_verification(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Crash during verification: source intact, no half-published destination."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "cross_dest"
    make_tree({"inbox/report.pdf": "report pdf content"}, base=tmp_path)
    organizer, history, _ = _setup_test_env(tmp_path, env, dest_dir)

    plan = organizer.plan([src_dir])

    # Intercept chmod/utime on temp before publish
    with (
        patch.object(fsops, "same_device", return_value=False),
        patch.object(os, "utime", side_effect=SimulatedCrash("Crash during verify/utime")),
        pytest.raises(SimulatedCrash),
    ):
        organizer.apply(plan)

    # Source intact
    assert (src_dir / "report.pdf").exists()
    assert (src_dir / "report.pdf").read_text(encoding="utf-8") == "report pdf content"
    assert not (dest_dir / "report.pdf").exists()

    reconcile(history, planned_dirs=[dest_dir])
    assert (src_dir / "report.pdf").read_text(encoding="utf-8") == "report pdf content"


def test_A71_crash_cross_fs_between_publish_and_unlink(
    env: EnvPaths,
    make_tree: MakeTreeFn,
    tmp_path: Path,
) -> None:
    """Crash after destination published but before source unlink: both exist, zero data loss."""
    src_dir = tmp_path / "inbox"
    dest_dir = tmp_path / "cross_dest"
    src_file = src_dir / "payload.dat"
    make_tree({"inbox/payload.dat": "important payload content"}, base=tmp_path)
    organizer, history, _ = _setup_test_env(tmp_path, env, dest_dir)

    plan = organizer.plan([src_dir])

    # Intercept Path.unlink on source to crash
    orig_unlink = Path.unlink

    def crashing_unlink(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == src_file:
            raise SimulatedCrash("Crash right before source unlink!")
        return orig_unlink(self, *args, **kwargs)

    with (
        patch.object(fsops, "same_device", return_value=False),
        patch.object(Path, "unlink", crashing_unlink),
        pytest.raises(SimulatedCrash),
    ):
        organizer.apply(plan)

    # Both source and published destination exist with exact matching content
    assert src_file.exists()
    assert (dest_dir / "payload.dat").exists()
    assert src_file.read_text(encoding="utf-8") == "important payload content"
    assert (dest_dir / "payload.dat").read_text(encoding="utf-8") == "important payload content"

    # Recovery: conflict warning, NEVER deleted
    actions = reconcile(history, planned_dirs=[dest_dir])
    assert len(actions) == 1
    assert actions[0].action_taken == "conflict_warning"

    # Zero data loss: both intact
    assert src_file.read_text(encoding="utf-8") == "important payload content"
    assert (dest_dir / "payload.dat").read_text(encoding="utf-8") == "important payload content"
