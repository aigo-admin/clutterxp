"""Watchdog event handling, filtering, mapping, and self-event suppression."""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from watchdog.events import (
    DirMovedEvent,
    FileClosedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)

from .ignore import IgnoreMatcher


def _to_path(p: bytes | str) -> Path:
    """Safely convert event path (bytes or str) to Path."""
    return Path(os.fsdecode(p))


class WatchEventKind(str, Enum):
    """Normalized action for watcher worker queue."""

    ARM = "arm"
    REARM = "rearm"
    SHORTEN = "shorten"
    CANCEL = "cancel"
    SCAN = "scan"


@dataclass(frozen=True, slots=True)
class WatcherEvent:
    """Event payload passed through thread-safe queue to worker."""

    kind: WatchEventKind
    path: Path
    folder: Path
    settle_seconds: float = 3.0


class SelfEventSuppression:
    """Thread-safe map from path to expiry timestamp (5 s default).

    Used to drop events caused by ClutterXP's own file operations.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._suppressed: dict[Path, float] = {}
        self._lock = threading.Lock()

    def suppress(self, path: Path, duration: float = 5.0) -> None:
        """Suppress events for path for duration seconds."""
        expiry = self._clock() + duration
        p = Path(path)
        with self._lock:
            self._suppressed[p] = expiry
            try:
                rp = p.resolve()
                if rp != p:
                    self._suppressed[rp] = expiry
            except OSError:
                pass

    def is_suppressed(self, path: Path) -> bool:
        """Return True if path is currently suppressed."""
        p = Path(path)
        now = self._clock()
        with self._lock:
            expiry = self._suppressed.get(p)
            if expiry is not None:
                if now < expiry:
                    return True
                del self._suppressed[p]

            try:
                rp = p.resolve()
                expiry = self._suppressed.get(rp)
                if expiry is not None:
                    if now < expiry:
                        return True
                    del self._suppressed[rp]
            except OSError:
                pass

            return False

    def clear(self) -> None:
        """Clear all suppressed paths."""
        with self._lock:
            self._suppressed.clear()


class WatchEventHandler(FileSystemEventHandler):
    """Filters incoming watchdog events and maps them to worker events."""

    def __init__(
        self,
        folder: Path,
        queue: queue.Queue[WatcherEvent],
        ignore_matcher: IgnoreMatcher,
        suppression: SelfEventSuppression,
        settle_seconds: float = 3.0,
        is_candidate: Callable[[Path], bool] | None = None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.queue = queue
        self.ignore_matcher = ignore_matcher
        self.suppression = suppression
        self.settle_seconds = settle_seconds
        self.is_candidate = is_candidate

    def _is_inside_folder(self, path: Path) -> bool:
        try:
            path.relative_to(self.folder)
            return True
        except ValueError:
            pass
        try:
            path.resolve().relative_to(self.folder.resolve())
            return True
        except (ValueError, OSError):
            return False

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = _to_path(event.src_path)
        if self.suppression.is_suppressed(path):
            return
        if self.ignore_matcher.matches(path):
            return
        self.queue.put(
            WatcherEvent(
                kind=WatchEventKind.ARM,
                path=path,
                folder=self.folder,
                settle_seconds=self.settle_seconds,
            )
        )

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = _to_path(event.src_path)
        if self.suppression.is_suppressed(path):
            return
        if self.ignore_matcher.matches(path):
            return
        if self.is_candidate is not None and not self.is_candidate(path):
            return
        self.queue.put(
            WatcherEvent(
                kind=WatchEventKind.REARM,
                path=path,
                folder=self.folder,
                settle_seconds=self.settle_seconds,
            )
        )

    def on_moved(self, event: DirMovedEvent | FileMovedEvent) -> None:
        if event.is_directory:
            return
        src_path = _to_path(event.src_path)
        dest_path = _to_path(event.dest_path)

        if not self.suppression.is_suppressed(src_path):
            self.queue.put(
                WatcherEvent(
                    kind=WatchEventKind.CANCEL,
                    path=src_path,
                    folder=self.folder,
                    settle_seconds=self.settle_seconds,
                )
            )

        if self.suppression.is_suppressed(dest_path):
            return
        if self._is_inside_folder(dest_path) and not self.ignore_matcher.matches(dest_path):
            self.queue.put(
                WatcherEvent(
                    kind=WatchEventKind.ARM,
                    path=dest_path,
                    folder=self.folder,
                    settle_seconds=self.settle_seconds,
                )
            )

    def on_closed(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = _to_path(event.src_path)
        if self.suppression.is_suppressed(path):
            return
        if self.ignore_matcher.matches(path):
            return
        if self.is_candidate is not None and not self.is_candidate(path):
            return
        self.queue.put(
            WatcherEvent(
                kind=WatchEventKind.SHORTEN,
                path=path,
                folder=self.folder,
                settle_seconds=self.settle_seconds,
            )
        )

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = _to_path(event.src_path)
        if self.suppression.is_suppressed(path):
            return
        self.queue.put(
            WatcherEvent(
                kind=WatchEventKind.CANCEL,
                path=path,
                folder=self.folder,
                settle_seconds=self.settle_seconds,
            )
        )

    def on_overflow(self) -> None:
        """Enqueues folder reconciliation scan on queue overflow."""
        self.queue.put(
            WatcherEvent(
                kind=WatchEventKind.SCAN,
                path=self.folder,
                folder=self.folder,
                settle_seconds=self.settle_seconds,
            )
        )


__all__ = [
    "DirMovedEvent",
    "FileClosedEvent",
    "FileCreatedEvent",
    "FileDeletedEvent",
    "FileModifiedEvent",
    "FileMovedEvent",
    "SelfEventSuppression",
    "WatchEventHandler",
    "WatchEventKind",
    "WatcherEvent",
]
