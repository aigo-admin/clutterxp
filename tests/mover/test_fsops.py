"""Tests for low-level filesystem primitives in fsops.py."""

from __future__ import annotations

import errno
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from clutterxp.mover import fsops


def test_rename_noreplace_success(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("hello world")

    assert fsops.rename_noreplace(src, dst) is True
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "hello world"


def test_rename_noreplace_eexist_returns_false(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("source content")
    dst.write_text("dest content")

    assert fsops.rename_noreplace(src, dst) is False
    assert src.exists()
    assert src.read_text() == "source content"
    assert dst.exists()
    assert dst.read_text() == "dest content"


def test_rename_noreplace_capability_cache(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("content")

    # When capability is cached as False, rename_noreplace immediately returns False
    with patch.dict(fsops._capability_cache, {"renameat2": False}):
        assert fsops.rename_noreplace(src, dst) is False
        assert src.exists()
        assert not dst.exists()

    # When renameat2 fails with ENOTSUP, it records False in _capability_cache
    with patch.dict(fsops._capability_cache, {}, clear=True):
        if fsops._libc is not None and hasattr(fsops._libc, "renameat2"):
            with (
                patch.object(fsops._libc, "renameat2", return_value=-1),
                patch("ctypes.get_errno", return_value=errno.ENOTSUP),
            ):
                assert fsops.rename_noreplace(src, dst) is False
                assert fsops._capability_cache.get("renameat2") is False


def test_link_unlink_success(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("linked content")

    assert fsops.link_unlink(src, dst) is True
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "linked content"


def test_link_unlink_dst_exists_returns_false(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("source content")
    dst.write_text("dest content")

    assert fsops.link_unlink(src, dst) is False
    assert src.exists()
    assert src.read_text() == "source content"
    assert dst.exists()
    assert dst.read_text() == "dest content"


def test_link_unlink_failure_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("precious data")

    # Simulate unlink failure
    def fake_unlink(path: Path) -> None:
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    assert fsops.link_unlink(src, dst) is False
    # CRITICAL DATA SAFETY: dst MUST NOT be deleted even if unlink failed
    assert dst.exists()
    assert dst.read_text() == "precious data"
    assert src.exists()


def test_link_unlink_capability_cache(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("test")

    with patch.dict(fsops._capability_cache, {"link": False}):
        assert fsops.link_unlink(src, dst) is False
        assert src.exists()
        assert not dst.exists()


def test_checked_rename_success(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("checked rename data")

    assert fsops.checked_rename(src, dst) is True
    assert not src.exists()
    assert dst.exists()
    assert dst.read_text() == "checked rename data"


def test_checked_rename_dst_exists_returns_false(tmp_path: Path) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("source")
    dst.write_text("dest")

    assert fsops.checked_rename(src, dst) is False
    assert src.exists()
    assert dst.exists()
    assert dst.read_text() == "dest"


def test_checked_rename_concurrent_access(tmp_path: Path) -> None:
    num_threads = 10
    dst = tmp_path / "target.txt"
    results: list[bool] = []
    sources: list[Path] = []

    for i in range(num_threads):
        s = tmp_path / f"src_{i}.txt"
        s.write_text(f"content {i}")
        sources.append(s)

    def worker(src: Path) -> None:
        res = fsops.checked_rename(src, dst)
        results.append(res)

    threads = [threading.Thread(target=worker, args=(s,)) for s in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread should succeed, all others fail
    assert results.count(True) == 1
    assert results.count(False) == num_threads - 1
    assert dst.exists()


def test_same_device(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")

    assert fsops.same_device(a, b) is True
    assert fsops.same_device(a, tmp_path / "nonexistent.txt") is True

    # Test with simulated different st_dev
    class FakeStat:
        def __init__(self, dev: int) -> None:
            self.st_dev = dev

    with patch.object(Path, "stat", side_effect=[FakeStat(1), FakeStat(2)]):
        assert fsops.same_device(a, b) is False


def test_rename_noreplace_libc_missing(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello")

    with patch.object(fsops, "_libc", None):
        assert fsops.rename_noreplace(src, dst) is False


def test_rename_noreplace_exdev_and_other_errors(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello")

    if fsops._libc is not None and hasattr(fsops._libc, "renameat2"):
        with (
            patch.dict(fsops._capability_cache, {"renameat2": True}),
            patch.object(fsops._libc, "renameat2", return_value=-1),
            patch("clutterxp.mover.fsops.ctypes.get_errno", return_value=errno.EXDEV),
        ):
            with pytest.raises(OSError) as exc_info:
                fsops.rename_noreplace(src, dst)
            assert exc_info.value.errno == errno.EXDEV

        with (
            patch.dict(fsops._capability_cache, {"renameat2": True}),
            patch.object(fsops._libc, "renameat2", return_value=-1),
            patch("clutterxp.mover.fsops.ctypes.get_errno", return_value=errno.EIO),
        ):
            with pytest.raises(OSError) as exc_info:
                fsops.rename_noreplace(src, dst)
            assert exc_info.value.errno == errno.EIO


def test_fsync_file_success(tmp_path: Path) -> None:
    f = tmp_path / "sync.txt"
    f.write_text("sync me")
    fsops.fsync_file(f)


def test_link_unlink_oserrors(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello")

    with patch.dict(fsops._capability_cache, {"link": True}):
        with patch("os.link", side_effect=OSError(errno.EEXIST, "File exists")):
            assert fsops.link_unlink(src, dst) is False

        with patch("os.link", side_effect=OSError(errno.ENOTSUP, "Not supported")):
            assert fsops.link_unlink(src, dst) is False
            assert fsops._capability_cache.get("link") is False

    with patch.dict(fsops._capability_cache, {"link": True}):
        with patch("os.link", side_effect=OSError(errno.EXDEV, "Cross-device link")):
            with pytest.raises(OSError) as exc_info:
                fsops.link_unlink(src, dst)
            assert exc_info.value.errno == errno.EXDEV

        with patch("os.link", side_effect=OSError(errno.EIO, "I/O error")):
            with pytest.raises(OSError) as exc_info:
                fsops.link_unlink(src, dst)
            assert exc_info.value.errno == errno.EIO


def test_checked_rename_oserrors(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst_nonexistent.txt"
    src.write_text("hello")

    with patch.object(Path, "rename", side_effect=FileExistsError("File exists")):
        assert fsops.checked_rename(src, dst) is False

    with patch.object(Path, "rename", side_effect=OSError(errno.EEXIST, "File exists")):
        assert fsops.checked_rename(src, dst) is False

    with patch.object(Path, "rename", side_effect=OSError(errno.EIO, "I/O error")):
        with pytest.raises(OSError) as exc_info:
            fsops.checked_rename(src, dst)
        assert exc_info.value.errno == errno.EIO


def test_fsync_dir_errors(tmp_path: Path) -> None:
    with patch("os.open", side_effect=OSError(errno.EACCES, "Denied")):
        # Must not raise
        fsops.fsync_dir(tmp_path)

    with patch("os.fsync", side_effect=OSError(errno.EINVAL, "Invalid")):
        # EINVAL must be suppressed
        fsops.fsync_dir(tmp_path)

    with patch("os.fsync", side_effect=OSError(errno.EIO, "I/O error")), pytest.raises(OSError):
        fsops.fsync_dir(tmp_path)
