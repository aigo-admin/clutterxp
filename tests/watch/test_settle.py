"""Unit tests for Settler using FakeClock and mock stat/proc."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from clutterxp.watch.settle import Settler, SettleStatus
from tests.helpers import FakeClock


@dataclass
class MockStat:
    st_size: int = 100
    st_mtime_ns: int = 1_000_000_000
    st_mode: int = stat.S_IFREG | 0o644


def test_settle_nonexistent_file() -> None:
    clock = FakeClock(0.0)
    settler = Settler(clock)
    p = Path("/tmp/does_not_exist_clutterxp.txt")

    assert settler.check(p, settle_seconds=3.0) == SettleStatus.GONE


def test_settle_drops_symlinks(tmp_path: Path) -> None:
    clock = FakeClock(0.0)
    settler = Settler(clock)

    target = tmp_path / "real.txt"
    target.write_text("content")

    symlink = tmp_path / "link.txt"
    symlink.symlink_to(target)

    assert settler.check(symlink, settle_seconds=3.0) == SettleStatus.DROP


def test_settle_drops_directories(tmp_path: Path) -> None:
    clock = FakeClock(0.0)
    settler = Settler(clock)

    d = tmp_path / "sub_dir"
    d.mkdir()

    assert settler.check(d, settle_seconds=3.0) == SettleStatus.DROP


def test_settle_stable_stat_two_samples() -> None:
    clock = FakeClock(0.0)
    current_stat = MockStat(st_size=200, st_mtime_ns=500)

    def fake_stat(_path: Path | str) -> MockStat:
        return current_stat

    settler = Settler(clock, stat_fn=fake_stat, sample_interval=0.5)
    p = Path("/tmp/test.txt")

    # Sample 1 at t=0.0: returns NOT_YET
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.NOT_YET

    # Sample 2 at t=0.3 (< 0.5s): returns NOT_YET
    clock.advance(0.3)
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.NOT_YET

    # Sample 3 at t=0.5 (>= 0.5s from sample 1): stat unchanged -> READY
    clock.advance(0.2)
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.READY


def test_settle_stat_change_resets_interval() -> None:
    clock = FakeClock(0.0)
    current_stat = MockStat(st_size=200, st_mtime_ns=500)

    def fake_stat(_path: Path | str) -> MockStat:
        return current_stat

    settler = Settler(clock, stat_fn=fake_stat, sample_interval=0.5)
    p = Path("/tmp/test.txt")

    # Sample 1 at t=0.0
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.NOT_YET

    # At t=0.4, file grows
    clock.advance(0.4)
    current_stat = MockStat(st_size=300, st_mtime_ns=600)
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.NOT_YET

    # At t=0.6 (only 0.2s after size change): still NOT_YET
    clock.advance(0.2)
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.NOT_YET

    # At t=0.9 (0.5s after size change): READY
    clock.advance(0.3)
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.READY


def test_settle_open_for_write_detection(tmp_path: Path) -> None:
    clock = FakeClock(0.0)
    test_file = tmp_path / "writing.dat"
    test_file.write_bytes(b"hello")

    mock_proc = tmp_path / "proc"
    pid_dir = mock_proc / "12345"
    fd_dir = pid_dir / "fd"
    fdinfo_dir = pid_dir / "fdinfo"
    fd_dir.mkdir(parents=True)
    fdinfo_dir.mkdir(parents=True)

    # fd 3 points to test_file
    (fd_dir / "3").symlink_to(test_file)
    # flags 02500002 is O_RDWR (write mode)
    (fdinfo_dir / "3").write_text("pos:\t0\nflags:\t02500002\n")

    settler = Settler(clock, proc_dir=mock_proc, sample_interval=0.5)

    # First sample at t=0: NOT_YET
    assert settler.check(test_file, settle_seconds=3.0) == SettleStatus.NOT_YET

    # At t=0.5: stat is stable, but file is open for write -> NOT_YET
    clock.advance(0.5)
    assert settler.is_open_for_write(test_file)
    assert settler.check(test_file, settle_seconds=3.0) == SettleStatus.NOT_YET

    # Close file / switch flags to O_RDONLY (00000000)
    (fdinfo_dir / "3").write_text("pos:\t0\nflags:\t00000000\n")
    assert not settler.is_open_for_write(test_file)

    # Check passes: READY
    assert settler.check(test_file, settle_seconds=3.0) == SettleStatus.READY


def test_settle_max_wait_drops_candidate() -> None:
    clock = FakeClock(0.0)
    current_stat = MockStat(st_size=10, st_mtime_ns=100)

    def fake_stat(_path: Path | str) -> MockStat:
        return current_stat

    settler = Settler(clock, stat_fn=fake_stat, max_wait=10.0, sample_interval=0.5)
    p = Path("/tmp/slow.txt")

    assert settler.check(p, settle_seconds=3.0) == SettleStatus.NOT_YET

    # Keep modifying file over time
    for t in range(1, 10):
        clock.advance(1.0)
        current_stat = MockStat(st_size=10 + t, st_mtime_ns=100 + t)
        assert settler.check(p, settle_seconds=3.0) == SettleStatus.NOT_YET

    # Advance past max_wait of 10.0s
    clock.advance(2.0)
    current_stat = MockStat(st_size=25, st_mtime_ns=125)
    assert settler.check(p, settle_seconds=3.0) == SettleStatus.DROP
