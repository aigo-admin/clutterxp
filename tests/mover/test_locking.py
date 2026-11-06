"""Tests for filesystem locking and watch locking in locking.py."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from clutterxp.locking import fs_lock, watch_lock
from clutterxp.models import ClutterError, ErrorCode


def test_fs_lock_basic(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"

    with fs_lock(lock_path):
        assert lock_path.exists()
        mode = lock_path.stat().st_mode & 0o777
        assert mode == 0o600

    # Lock can be re-acquired after release
    with fs_lock(lock_path):
        assert lock_path.exists()


def test_fs_lock_timeout(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    acquired_event = threading.Event()
    release_event = threading.Event()

    def lock_holder() -> None:
        with fs_lock(lock_path):
            acquired_event.set()
            release_event.wait(timeout=2.0)

    t = threading.Thread(target=lock_holder)
    t.start()
    try:
        assert acquired_event.wait(timeout=2.0) is True

        with pytest.raises(ClutterError) as exc_info, fs_lock(lock_path, timeout=0.1):
            pass

        assert exc_info.value.code == ErrorCode.LOCK_TIMEOUT
    finally:
        release_event.set()
        t.join()


def test_fs_lock_permissions_0600(tmp_path: Path) -> None:
    lock_path = tmp_path / "perm.lock"
    with fs_lock(lock_path):
        assert (lock_path.stat().st_mode & 0o777) == 0o600


def test_fs_lock_kernel_release_on_exit(tmp_path: Path) -> None:
    """Kernel releases flock when process exits."""
    lock_path = tmp_path / "proc.lock"

    # Start a subprocess that acquires fs_lock and sleeps 0.2s then exits
    code = f"""
import sys, time
from pathlib import Path
sys.path.insert(0, {str(Path.cwd() / "src")!r})
from clutterxp.locking import fs_lock

with fs_lock(Path({str(lock_path)!r})):
    time.sleep(0.3)
"""
    proc = subprocess.Popen([sys.executable, "-c", code])
    time.sleep(0.1)

    # While child is running, lock is held
    with pytest.raises(ClutterError), fs_lock(lock_path, timeout=0.05):
        pass

    proc.wait(timeout=2.0)

    # After child exits, parent acquires immediately
    with fs_lock(lock_path, timeout=1.0):
        pass


def test_watch_lock_holder_pid_message(tmp_path: Path) -> None:
    lock_path = tmp_path / "watch.lock"

    with watch_lock(lock_path):
        assert lock_path.exists()
        assert (lock_path.stat().st_mode & 0o777) == 0o600
        content = lock_path.read_text().strip()
        assert content == str(os.getpid())

        # Second acquisition in same or other process must fail with holder PID
        with pytest.raises(ClutterError) as exc_info, watch_lock(lock_path):
            pass

        assert exc_info.value.code == ErrorCode.LOCK_TIMEOUT
        assert str(os.getpid()) in exc_info.value.message


def test_watch_lock_kernel_release_on_exit(tmp_path: Path) -> None:
    """Kernel releases watch lock on process exit / kill."""
    lock_path = tmp_path / "watch_proc.lock"

    code = f"""
import sys, time
from pathlib import Path
sys.path.insert(0, {str(Path.cwd() / "src")!r})
from clutterxp.locking import watch_lock

with watch_lock(Path({str(lock_path)!r})):
    print("READY", flush=True)
    time.sleep(5)
"""
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert "READY" in line

        # Lock is held
        with pytest.raises(ClutterError) as exc_info, watch_lock(lock_path):
            pass
        assert str(proc.pid) in exc_info.value.message

        # Kill the child process (simulate crash / termination)
        proc.kill()
        proc.wait(timeout=2.0)

        # Main process can acquire immediately
        with watch_lock(lock_path):
            assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
