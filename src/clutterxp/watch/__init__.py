"""Watcher: filesystem monitoring, debounce queue, settle checks, and worker lifecycle."""

from __future__ import annotations

from .debounce import Debouncer
from .events import (
    FileClosedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    SelfEventSuppression,
    WatcherEvent,
    WatchEventHandler,
    WatchEventKind,
)
from .ignore import BUILTIN_IGNORE, IgnoreMatcher
from .service import WatchService
from .settle import Settler, SettleStatus

__all__ = [
    "BUILTIN_IGNORE",
    "Debouncer",
    "FileClosedEvent",
    "FileCreatedEvent",
    "FileDeletedEvent",
    "FileModifiedEvent",
    "FileMovedEvent",
    "IgnoreMatcher",
    "SelfEventSuppression",
    "SettleStatus",
    "Settler",
    "WatchEventHandler",
    "WatchEventKind",
    "WatchService",
    "WatcherEvent",
]
