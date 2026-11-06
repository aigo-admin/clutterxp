"""Settler: file completion and stability detection."""

from __future__ import annotations

import logging
import os
import stat
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SettleStatus(str, Enum):
    """Result of checking whether a file is settled and ready to move."""

    READY = "ready"
    NOT_YET = "not_yet"
    GONE = "gone"
    DROP = "drop"


class Settler:
    """Verifies file stability (stat checks and /proc open-for-write checks)."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        stat_fn: Callable[[Path | str], Any] = os.lstat,
        proc_dir: Path = Path("/proc"),
        *,
        sample_interval: float = 0.5,
        max_wait: float = 6 * 3600.0,
    ) -> None:
        self._clock = clock
        self._stat_fn = stat_fn
        self._proc_dir = proc_dir
        self.sample_interval = sample_interval
        self.max_wait = max_wait
        self._samples: dict[Path, tuple[tuple[int, int], float]] = {}
        self._first_seen: dict[Path, float] = {}

    def clear(self, path: Path) -> None:
        """Clear recorded samples and first-seen timestamp for path."""
        self._samples.pop(path, None)
        self._first_seen.pop(path, None)

    def check(self, path: Path, settle_seconds: float) -> SettleStatus:
        """Check whether candidate file is settled and safe to move.

        Steps:
        1. Regular file check: must exist, regular file, not symlink.
        2. Stable stat check: 2 samples >= sample_interval apart with identical (size, mtime_ns).
        3. Open for write check: no process on Linux holds path open for write.
        4. If all pass -> READY.
        """
        # Step 1: Check existence and regular file status
        try:
            st = self._stat_fn(path)
        except (FileNotFoundError, OSError):
            self.clear(path)
            return SettleStatus.GONE

        is_symlink = getattr(st, "is_symlink", False)
        is_regular = getattr(st, "is_regular", True)
        mode = getattr(st, "st_mode", None)
        if mode is not None:
            is_symlink = stat.S_ISLNK(mode)
            is_regular = stat.S_ISREG(mode)

        if is_symlink or not is_regular:
            self.clear(path)
            return SettleStatus.DROP

        now = self._clock()

        # Track continuous activity and enforce max_wait
        if path not in self._first_seen:
            self._first_seen[path] = now
        elif (now - self._first_seen[path]) > self.max_wait:
            logger.warning(
                "Candidate %s has not settled after %s seconds; dropping",
                path,
                self.max_wait,
            )
            self.clear(path)
            return SettleStatus.DROP

        # Step 2: Two-sample stable stat check
        size = getattr(st, "st_size", getattr(st, "size", 0))
        mtime_ns = getattr(st, "st_mtime_ns", getattr(st, "mtime_ns", 0))
        current_stat = (size, mtime_ns)

        interval = min(self.sample_interval, settle_seconds) if settle_seconds > 0 else 0.0

        if path not in self._samples:
            self._samples[path] = (current_stat, now)
            return SettleStatus.NOT_YET

        prev_stat, prev_time = self._samples[path]
        if current_stat != prev_stat:
            self._samples[path] = (current_stat, now)
            return SettleStatus.NOT_YET

        if (now - prev_time) < interval:
            return SettleStatus.NOT_YET

        # Step 3: Not open for writing
        if self.is_open_for_write(path):
            return SettleStatus.NOT_YET

        # Step 4: Ready
        self.clear(path)
        return SettleStatus.READY

    def is_open_for_write(self, path: Path) -> bool:
        """Inspect /proc to determine if any process holds path open for write."""
        if not self._proc_dir.exists() or not self._proc_dir.is_dir():
            return False

        try:
            target_real = os.path.realpath(str(path))
        except OSError:
            return False

        try:
            proc_entries = list(self._proc_dir.iterdir())
        except OSError:
            return False

        for pid_entry in proc_entries:
            if not pid_entry.name.isdigit():
                continue
            fd_dir = pid_entry / "fd"
            try:
                fd_entries = list(fd_dir.iterdir())
            except OSError:
                continue

            for fd_path in fd_entries:
                try:
                    link_dest = str(fd_path.readlink())
                    if link_dest == target_real or link_dest == f"{target_real} (deleted)":
                        fdinfo_path = pid_entry / "fdinfo" / fd_path.name
                        flags = self._read_fdinfo_flags(fdinfo_path)
                        if flags is not None and (flags & os.O_ACCMODE) != os.O_RDONLY:
                            return True
                except OSError:
                    continue

        return False

    def _read_fdinfo_flags(self, fdinfo_path: Path) -> int | None:
        """Read and parse octal 'flags:' field from /proc/<pid>/fdinfo/<fd>."""
        try:
            with fdinfo_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("flags:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1], 8)
        except OSError:
            pass
        return None


__all__ = ["SettleStatus", "Settler"]
