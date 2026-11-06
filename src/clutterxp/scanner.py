"""Scanner module for directory traversal and FileInfo generation.

Lists directory entries using os.scandir, applies ignore patterns and hidden
file filters, and yields FileInfo objects. Honors docs/03 and docs/09.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import stat
from collections.abc import Iterator, Sequence
from pathlib import Path

from clutterxp.config.schema import Settings
from clutterxp.models import FileInfo

BUILTIN_IGNORE_PATTERNS: tuple[str, ...] = (
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


def _matches_any_pattern(name: str, patterns: Sequence[str], *, case_sensitive: bool) -> bool:
    """Check if filename matches any of the given glob patterns."""
    if case_sensitive:
        return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)
    name_folded = name.casefold()
    return any(fnmatch.fnmatchcase(name_folded, pat.casefold()) for pat in patterns)


def scan(
    folder: Path,
    *,
    recursive: bool = False,
    settings: Settings | None = None,
    ignore_patterns: Sequence[str] = (),
) -> Iterator[FileInfo]:
    """Scan folder and yield FileInfo for each entry.

    - Uses os.scandir.
    - Performs one lstat per yielded entry.
    - Honors built-in ignore patterns + settings.ignore + ignore_patterns.
    - If settings.skip_hidden is True (default), skips files/directories with a leading dot.
    - Never descends into hidden directories.
    - Never follows symlinked directories.
    - Non-regular files (symlinks unless follow_symlinks, dirs when non-recursive, fifos, sockets)
      are yielded with is_regular=False so the rule engine can mark them IGNORED.
    - Recursion only occurs when recursive is True.
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return

    skip_hidden = settings.skip_hidden if settings is not None else True
    follow_symlinks = settings.follow_symlinks if settings is not None else False
    case_sensitive = settings.case_sensitive if settings is not None else False

    all_ignores: list[str] = list(BUILTIN_IGNORE_PATTERNS)
    if settings is not None and settings.ignore:
        all_ignores.extend(settings.ignore)
    if ignore_patterns:
        all_ignores.extend(ignore_patterns)

    try:
        with os.scandir(folder_path) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return

    for entry in entries:
        name = entry.name

        # Hidden file / directory check
        if skip_hidden and name.startswith("."):
            continue

        # Ignore patterns check
        if _matches_any_pattern(name, all_ignores, case_sensitive=case_sensitive):
            continue

        entry_path = Path(entry.path)

        is_symlink = False
        with contextlib.suppress(OSError):
            is_symlink = entry.is_symlink()

        if is_symlink:
            is_symlinked_dir = False
            with contextlib.suppress(OSError):
                is_symlinked_dir = entry.is_dir(follow_symlinks=True)

            if is_symlinked_dir:
                # Symlinked directories: NEVER descend into them
                try:
                    st = entry_path.lstat()
                except OSError:
                    continue
                yield FileInfo(
                    path=entry_path,
                    name=entry_path.name,
                    stem=entry_path.stem,
                    ext="",
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                    inode=st.st_ino,
                    device=st.st_dev,
                    is_regular=False,
                    is_symlink=True,
                )
                continue

        is_dir = False
        with contextlib.suppress(OSError):
            is_dir = entry.is_dir(follow_symlinks=False)

        if is_dir:
            # Real directory
            if recursive:
                yield from scan(
                    entry_path,
                    recursive=True,
                    settings=settings,
                    ignore_patterns=ignore_patterns,
                )
            else:
                try:
                    st = entry_path.lstat()
                except OSError:
                    continue
                yield FileInfo(
                    path=entry_path,
                    name=entry_path.name,
                    stem=entry_path.stem,
                    ext="",
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                    inode=st.st_ino,
                    device=st.st_dev,
                    is_regular=False,
                    is_symlink=False,
                )
            continue

        # Non-directory entries
        if is_symlink:
            is_reg = False
            st_target = None
            if follow_symlinks:
                with contextlib.suppress(OSError):
                    st_target = entry_path.stat()
                    is_reg = stat.S_ISREG(st_target.st_mode)

            try:
                st = entry_path.lstat()
            except OSError:
                continue

            stat_info = st_target if (is_reg and st_target is not None) else st
            suffix = entry_path.suffix
            yield FileInfo(
                path=entry_path,
                name=entry_path.name,
                stem=entry_path.stem,
                ext=suffix[1:].lower() if suffix else "",
                size=stat_info.st_size,
                mtime_ns=stat_info.st_mtime_ns,
                inode=stat_info.st_ino,
                device=stat_info.st_dev,
                is_regular=is_reg,
                is_symlink=True,
            )
            continue

        # Regular files, FIFOs, sockets, devices
        try:
            yield FileInfo.from_path(entry_path)
        except OSError:
            continue


__all__ = ["BUILTIN_IGNORE_PATTERNS", "scan"]
