"""Ignore pattern matching for watch service (built-ins, hidden files, user globs)."""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from pathlib import Path

BUILTIN_IGNORE: tuple[str, ...] = (
    "*.crdownload",
    "*.part",
    "*.partial",
    "*.tmp",
    "*.temp",
    "*.download",
    "*.opdownload",
    "*.!qB",
    "*.aria2",
    "*.swp",
    "*.swx",
    "*~",
    "~$*",
    ".~lock.*#",
    "*.clutterxp-partial",
)


class IgnoreMatcher:
    """Matches files against built-in and configured ignore patterns."""

    def __init__(
        self,
        builtin: Sequence[str] = BUILTIN_IGNORE,
        global_extra: Sequence[str] = (),
        folder_extra: Sequence[str] = (),
        skip_hidden: bool = True,
    ) -> None:
        self.builtin = tuple(builtin)
        self.global_extra = tuple(global_extra)
        self.folder_extra = tuple(folder_extra)
        self.skip_hidden = skip_hidden
        self._patterns = tuple(
            p.casefold() for p in (*self.builtin, *self.global_extra, *self.folder_extra)
        )

    def matches(self, path: Path) -> bool:
        """Return True if path should be ignored."""
        name = path.name
        if not name:
            return True

        # ClutterXP's internal partial transfer files are always ignored
        name_cf = name.casefold()
        if name_cf.endswith(".clutterxp-partial"):
            return True

        # Skip hidden files or files within hidden directories
        if self.skip_hidden:
            for part in path.parts:
                if part.startswith(".") and part not in (".", ".."):
                    return True

        # Casefolded fnmatch matching on path.name
        for pattern in self._patterns:
            if fnmatch.fnmatchcase(name_cf, pattern):
                return True
            if "/" in pattern:
                posix_path = path.as_posix().casefold()
                if fnmatch.fnmatchcase(posix_path, pattern) or fnmatch.fnmatchcase(
                    posix_path, f"*/{pattern.lstrip('/')}"
                ):
                    return True

        return False


__all__ = ["BUILTIN_IGNORE", "IgnoreMatcher"]
