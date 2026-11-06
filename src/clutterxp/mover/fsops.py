"""Low-level filesystem operations with atomic no-clobber primitives."""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# renameat2 constants (from linux/fs.h and fcntl.h)
AT_FDCWD = -100
RENAME_NOREPLACE = 1

_capability_cache: dict[str, bool] = {}
_capability_lock = threading.Lock()
_checked_rename_lock = threading.Lock()

# Load libc
_libc: ctypes.CDLL | None = None
try:
    _libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(_libc, "renameat2"):
        _libc.renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        _libc.renameat2.restype = ctypes.c_int
    else:
        _capability_cache["renameat2"] = False
except Exception:
    _capability_cache["renameat2"] = False


def rename_noreplace(src: Path, dst: Path) -> bool:
    """Attempt renameat2(RENAME_NOREPLACE). Returns True on success.

    Returns False if EEXIST/ENOTSUP/EPERM (caller tries next strategy).
    Uses _capability_cache after first failure of this call.
    """
    with _capability_lock:
        if _capability_cache.get("renameat2") is False:
            return False

    if _libc is None or not hasattr(_libc, "renameat2"):
        with _capability_lock:
            _capability_cache["renameat2"] = False
        return False

    src_bytes = os.fsencode(src)
    dst_bytes = os.fsencode(dst)

    res = _libc.renameat2(AT_FDCWD, src_bytes, AT_FDCWD, dst_bytes, RENAME_NOREPLACE)
    if res == 0:
        with _capability_lock:
            _capability_cache["renameat2"] = True
        return True

    err = ctypes.get_errno()
    if err == errno.EEXIST:
        # Destination exists. Syscall is supported!
        with _capability_lock:
            _capability_cache["renameat2"] = True
        return False
    elif err in (errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL, errno.EPERM):
        # Syscall or flag not supported on this kernel/filesystem
        with _capability_lock:
            _capability_cache["renameat2"] = False
        return False
    elif err == errno.EXDEV:
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), str(src))
    else:
        raise OSError(err, os.strerror(err), str(src))


def link_unlink(src: Path, dst: Path) -> bool:
    """os.link(src, dst) [fails if dst exists], then os.unlink(src).

    If unlink fails after successful link: log warning but do NOT delete dst (data safety).
    Returns True on success.
    """
    with _capability_lock:
        if _capability_cache.get("link") is False:
            return False

    try:
        os.link(src, dst, follow_symlinks=False)
    except (FileExistsError, OSError) as err:
        if isinstance(err, FileExistsError) or err.errno == errno.EEXIST:
            with _capability_lock:
                _capability_cache["link"] = True
            return False
        elif err.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
            with _capability_lock:
                _capability_cache["link"] = False
            return False
        elif err.errno == errno.EXDEV:
            raise
        else:
            raise

    with _capability_lock:
        _capability_cache["link"] = True

    try:
        src.unlink()
    except OSError as err:
        logger.warning(
            "Failed to unlink source '%s' after link to '%s': %s (kept for safety)",
            src,
            dst,
            err,
        )
        return False

    return True


def checked_rename(src: Path, dst: Path) -> bool:
    """Under a per-process lock: check dst does not exist, then os.rename.

    Returns True on success. This is a last resort (not atomic, but protected within process).
    """
    with _checked_rename_lock:
        if os.path.lexists(dst):
            return False
        try:
            src.rename(dst)
            return True
        except (FileExistsError, OSError) as e:
            if isinstance(e, FileExistsError) or e.errno == errno.EEXIST:
                return False
            raise


def _dev_of(p: Path) -> int:
    curr = p
    while not curr.exists() and curr != curr.parent:
        curr = curr.parent
    return curr.stat().st_dev


def same_device(a: Path, b: Path) -> bool:
    """True if a and b are on the same filesystem (st_dev comparison)."""
    return _dev_of(a) == _dev_of(b)


def fsync_file(path: Path) -> None:
    """Open file and fsync, then close."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path: Path) -> None:
    """Open directory and fsync."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
            raise
    finally:
        os.close(fd)


__all__ = [
    "AT_FDCWD",
    "RENAME_NOREPLACE",
    "_capability_cache",
    "checked_rename",
    "fsync_dir",
    "fsync_file",
    "link_unlink",
    "rename_noreplace",
    "same_device",
]
