"""Debouncer: pure in-memory deadline map for filesystem event debounce."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path


class Debouncer:
    """Manages path -> deadline mapping for event debouncing without internal threads."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        max_entries: int = 10_000,
    ) -> None:
        self._clock = clock
        self.max_entries = max_entries
        self._entries: dict[Path, float] = {}
        self.overflow: bool = False

    def arm(self, path: Path, delay: float) -> None:
        """Arm or re-arm path with deadline = clock() + delay.

        If capacity is reached, sets overflow=True and drops the oldest entry.
        """
        deadline = self._clock() + delay
        if path in self._entries:
            self._entries[path] = deadline
            return

        if len(self._entries) >= self.max_entries:
            self.overflow = True
            oldest = next(iter(self._entries))
            del self._entries[oldest]

        self._entries[path] = deadline

    def cancel(self, path: Path) -> None:
        """Remove path from debouncer if present."""
        self._entries.pop(path, None)

    def due(self, now: float | None = None) -> list[Path]:
        """Return and remove all paths whose deadline <= now, ordered by deadline."""
        if now is None:
            now = self._clock()

        due_items = [(p, dl) for p, dl in self._entries.items() if dl <= now]
        if not due_items:
            return []

        # Sort by earliest deadline first
        due_items.sort(key=lambda item: item[1])
        due_paths = [p for p, _ in due_items]

        for p in due_paths:
            del self._entries[p]

        return due_paths

    def is_empty(self) -> bool:
        """Return True if no entries are currently armed."""
        return len(self._entries) == 0

    def next_deadline(self) -> float | None:
        """Return earliest deadline among armed entries, or None if empty."""
        if not self._entries:
            return None
        return min(self._entries.values())

    def get_deadline(self, path: Path) -> float | None:
        """Return the current deadline for path, or None if not armed."""
        return self._entries.get(path)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, path: Path) -> bool:
        return path in self._entries


__all__ = ["Debouncer"]
