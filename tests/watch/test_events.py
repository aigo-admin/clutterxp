"""Unit tests for SelfEventSuppression and WatchEventHandler."""

from __future__ import annotations

import queue
from pathlib import Path

from watchdog.events import (
    DirCreatedEvent,
    FileClosedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from clutterxp.watch.events import (
    SelfEventSuppression,
    WatcherEvent,
    WatchEventHandler,
    WatchEventKind,
)
from clutterxp.watch.ignore import IgnoreMatcher
from tests.helpers import FakeClock


def test_self_event_suppression() -> None:
    clock = FakeClock(10.0)
    suppression = SelfEventSuppression(clock)

    p = Path("/tmp/moved_file.txt")
    suppression.suppress(p, duration=5.0)

    # At t=10.0: suppressed
    assert suppression.is_suppressed(p)

    # At t=14.9: still suppressed
    clock.advance(4.9)
    assert suppression.is_suppressed(p)

    # At t=15.1: expired
    clock.advance(0.2)
    assert not suppression.is_suppressed(p)


def test_handler_ignores_directories() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=SelfEventSuppression(),
    )

    dir_event = DirCreatedEvent("/watched/new_subdir")
    handler.dispatch(dir_event)

    assert q.empty()


def test_handler_created_and_modified_events() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    candidates = {Path("/watched/candidate.txt")}

    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=SelfEventSuppression(),
        settle_seconds=3.0,
        is_candidate=lambda p: p in candidates,
    )

    # Created event enqueues ARM
    handler.dispatch(FileCreatedEvent("/watched/new_file.pdf"))
    ev = q.get_nowait()
    assert ev.kind == WatchEventKind.ARM
    assert ev.path == Path("/watched/new_file.pdf")

    # Modified event on non-candidate is ignored
    handler.dispatch(FileModifiedEvent("/watched/not_candidate.txt"))
    assert q.empty()

    # Modified event on candidate enqueues REARM
    handler.dispatch(FileModifiedEvent("/watched/candidate.txt"))
    ev2 = q.get_nowait()
    assert ev2.kind == WatchEventKind.REARM
    assert ev2.path == Path("/watched/candidate.txt")


def test_handler_moved_event() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=SelfEventSuppression(),
    )

    # Move within watched folder (e.g. browser .part to .pdf)
    handler.dispatch(FileMovedEvent("/watched/download.part", "/watched/download.pdf"))

    # src cancelled
    ev1 = q.get_nowait()
    assert ev1.kind == WatchEventKind.CANCEL
    assert ev1.path == Path("/watched/download.part")

    # dest armed
    ev2 = q.get_nowait()
    assert ev2.kind == WatchEventKind.ARM
    assert ev2.path == Path("/watched/download.pdf")


def test_handler_moved_outside_watched_folder() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=SelfEventSuppression(),
    )

    # File moved from watched folder to outside directory
    handler.dispatch(FileMovedEvent("/watched/file.txt", "/somewhere/else/file.txt"))

    # src cancelled, dest NOT armed
    ev = q.get_nowait()
    assert ev.kind == WatchEventKind.CANCEL
    assert ev.path == Path("/watched/file.txt")
    assert q.empty()


def test_handler_closed_and_deleted_events() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    candidates = {Path("/watched/active.txt")}

    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=SelfEventSuppression(),
        is_candidate=lambda p: p in candidates,
    )

    # FileClosedEvent enqueues SHORTEN for candidate
    handler.dispatch(FileClosedEvent("/watched/active.txt"))
    ev1 = q.get_nowait()
    assert ev1.kind == WatchEventKind.SHORTEN
    assert ev1.path == Path("/watched/active.txt")

    # FileDeletedEvent enqueues CANCEL
    handler.dispatch(FileDeletedEvent("/watched/active.txt"))
    ev2 = q.get_nowait()
    assert ev2.kind == WatchEventKind.CANCEL
    assert ev2.path == Path("/watched/active.txt")


def test_handler_drops_ignored_and_suppressed() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    clock = FakeClock(10.0)
    suppression = SelfEventSuppression(clock)

    suppressed_file = Path("/watched/moved_out.pdf")
    suppression.suppress(suppressed_file, duration=5.0)

    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=suppression,
    )

    # Ignored crdownload is dropped
    handler.dispatch(FileCreatedEvent("/watched/file.crdownload"))
    assert q.empty()

    # Suppressed file is dropped
    handler.dispatch(FileCreatedEvent("/watched/moved_out.pdf"))
    assert q.empty()


def test_suppression_clear() -> None:
    suppression = SelfEventSuppression()
    p = Path("/tmp/test.txt")
    suppression.suppress(p, duration=10.0)
    assert suppression.is_suppressed(p)
    suppression.clear()
    assert not suppression.is_suppressed(p)


def test_handler_on_overflow() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=SelfEventSuppression(),
    )
    handler.on_overflow()
    ev = q.get_nowait()
    assert ev.kind == WatchEventKind.SCAN
    assert ev.folder == folder


def test_handler_moved_with_suppressed_paths() -> None:
    folder = Path("/watched")
    q: queue.Queue[WatcherEvent] = queue.Queue()
    suppression = SelfEventSuppression()

    src = Path("/watched/src.txt")
    dst = Path("/watched/dst.txt")
    suppression.suppress(src, duration=10.0)
    suppression.suppress(dst, duration=10.0)

    handler = WatchEventHandler(
        folder=folder,
        queue=q,
        ignore_matcher=IgnoreMatcher(),
        suppression=suppression,
    )

    handler.dispatch(FileMovedEvent(str(src), str(dst)))
    # Both are suppressed -> queue stays empty
    assert q.empty()
