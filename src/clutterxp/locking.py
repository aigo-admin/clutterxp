"""Locking utilities: filesystem exclusive lock and watch lock."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from clutterxp.models import ClutterError, ErrorCode


def _open_lock_file(lock_path: Path) -> int:
    """Open or create a lock file with 0600 permissions."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with contextlib.suppress(OSError):
        lock_path.chmod(0o600)
    return fd


@contextmanager
def fs_lock(lock_path: Path, timeout: float = 10.0) -> Generator[None, None, None]:
    """Exclusive flock, timeout raises ClutterError(code=LOCK_TIMEOUT).

    Lock file created 0600. Released by kernel on crash.
    """
    fd = _open_lock_file(lock_path)
    start = time.monotonic()
    deadline = start + timeout
    acquired = False

    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError) as e:
                err = getattr(e, "errno", None)
                if err not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
            if time.monotonic() >= deadline:
                break
            rem = deadline - time.monotonic()
            if rem <= 0:
                break
            time.sleep(min(0.05, max(0.001, rem)))

        if not acquired:
            raise ClutterError(
                f"Could not acquire filesystem lock at {lock_path} within {timeout}s",
                code=ErrorCode.LOCK_TIMEOUT,
            )

        yield
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


@contextmanager
def watch_lock(lock_path: Path) -> Generator[None, None, None]:
    """Non-blocking flock. Raises ClutterError with PID of holder if locked.

    Writes own PID to lock file. Lock file 0600.
    """
    fd = _open_lock_file(lock_path)
    acquired = False

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, OSError) as e:
            err = getattr(e, "errno", None)
            if err in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                pid_holder = "unknown"
                with contextlib.suppress(OSError), lock_path.open(encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        pid_holder = content
                raise ClutterError(
                    f"Watch lock at {lock_path} is already held by PID {pid_holder}",
                    code=ErrorCode.LOCK_TIMEOUT,
                ) from None
            raise

        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.fsync(fd)

        yield
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                os.ftruncate(fd, 0)
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


__all__ = ["fs_lock", "watch_lock"]
