"""Unit tests for Debouncer using FakeClock."""

from __future__ import annotations

from pathlib import Path

from clutterxp.watch.debounce import Debouncer
from tests.helpers import FakeClock


def test_arm_and_is_empty() -> None:
    clock = FakeClock(100.0)
    debouncer = Debouncer(clock)

    assert debouncer.is_empty()
    assert len(debouncer) == 0

    p1 = Path("/tmp/file1.txt")
    debouncer.arm(p1, delay=3.0)

    assert not debouncer.is_empty()
    assert len(debouncer) == 1
    assert p1 in debouncer
    assert debouncer.get_deadline(p1) == 103.0
    assert debouncer.next_deadline() == 103.0


def test_rearm_updates_deadline() -> None:
    clock = FakeClock(10.0)
    debouncer = Debouncer(clock)
    p = Path("/tmp/file.txt")

    debouncer.arm(p, delay=3.0)
    assert debouncer.get_deadline(p) == 13.0

    clock.advance(1.0)  # now 11.0
    debouncer.arm(p, delay=4.0)
    assert debouncer.get_deadline(p) == 15.0
    assert len(debouncer) == 1


def test_cancel() -> None:
    clock = FakeClock(0.0)
    debouncer = Debouncer(clock)
    p = Path("/tmp/file.txt")

    debouncer.arm(p, delay=5.0)
    assert p in debouncer

    debouncer.cancel(p)
    assert p not in debouncer
    assert debouncer.is_empty()

    # Cancel nonexistent does not raise
    debouncer.cancel(Path("/tmp/nonexistent.txt"))


def test_due_ordering_and_removal() -> None:
    clock = FakeClock(0.0)
    debouncer = Debouncer(clock)

    p1 = Path("/tmp/p1.txt")
    p2 = Path("/tmp/p2.txt")
    p3 = Path("/tmp/p3.txt")

    debouncer.arm(p1, delay=5.0)  # deadline 5.0
    debouncer.arm(p2, delay=2.0)  # deadline 2.0
    debouncer.arm(p3, delay=8.0)  # deadline 8.0

    # At t=1.0: none due
    clock.advance(1.0)
    assert debouncer.due() == []

    # At t=3.0: p2 is due
    clock.advance(2.0)  # now 3.0
    due = debouncer.due()
    assert due == [p2]
    assert p2 not in debouncer
    assert len(debouncer) == 2

    # At t=6.0: p1 is due
    clock.advance(3.0)  # now 6.0
    due = debouncer.due()
    assert due == [p1]
    assert p1 not in debouncer

    # At t=10.0: p3 is due
    clock.advance(4.0)  # now 10.0
    due = debouncer.due()
    assert due == [p3]
    assert debouncer.is_empty()


def test_due_multiple_ordered_by_deadline() -> None:
    clock = FakeClock(0.0)
    debouncer = Debouncer(clock)

    p_late = Path("/tmp/late.txt")
    p_early = Path("/tmp/early.txt")

    debouncer.arm(p_late, delay=6.0)
    debouncer.arm(p_early, delay=2.0)

    clock.advance(10.0)
    due = debouncer.due()
    # Earliest deadline first
    assert due == [p_early, p_late]
    assert debouncer.is_empty()


def test_overflow() -> None:
    clock = FakeClock(0.0)
    debouncer = Debouncer(clock, max_entries=3)

    p1 = Path("/tmp/1.txt")
    p2 = Path("/tmp/2.txt")
    p3 = Path("/tmp/3.txt")
    p4 = Path("/tmp/4.txt")

    debouncer.arm(p1, 5.0)
    debouncer.arm(p2, 5.0)
    debouncer.arm(p3, 5.0)

    assert not debouncer.overflow
    assert len(debouncer) == 3

    # Adding 4th entry triggers overflow and drops oldest (p1)
    debouncer.arm(p4, 5.0)
    assert debouncer.overflow
    assert len(debouncer) == 3
    assert p1 not in debouncer
    assert p2 in debouncer
    assert p3 in debouncer
    assert p4 in debouncer
