"""Tests for scanner module."""

from __future__ import annotations

import os
from pathlib import Path

from clutterxp.config.schema import Settings
from clutterxp.models import FileInfo
from clutterxp.scanner import BUILTIN_IGNORE_PATTERNS, scan


def test_scan_basic_files(tmp_path: Path) -> None:
    f1 = tmp_path / "hello.txt"
    f1.write_text("hello world")
    f2 = tmp_path / "archive.tar.gz"
    f2.write_bytes(b"dummy")

    infos = list(scan(tmp_path))
    names = {info.name for info in infos}
    assert names == {"hello.txt", "archive.tar.gz"}

    txt_info = next(i for i in infos if i.name == "hello.txt")
    assert txt_info.stem == "hello"
    assert txt_info.ext == "txt"
    assert txt_info.size == len("hello world")
    assert txt_info.is_regular is True
    assert txt_info.is_symlink is False
    assert txt_info.inode > 0
    assert txt_info.device > 0


def test_scan_builtin_ignore_patterns(tmp_path: Path) -> None:
    for pat in BUILTIN_IGNORE_PATTERNS:
        # Construct sample filenames matching pattern
        if pat == ".~lock.*#":
            clean = ".~lock.document.docx#"
        elif pat == "*~":
            clean = "backup~"
        elif pat == "~$*":
            clean = "~$temp.docx"
        else:
            clean = pat.replace("*", "sample").replace("?", "x")
        f = tmp_path / clean
        f.write_text("data")

    # Also normal file that must not be ignored
    normal = tmp_path / "normal_file.pdf"
    normal.write_text("data")

    infos = list(scan(tmp_path))
    names = [i.name for i in infos]
    assert names == ["normal_file.pdf"]


def test_scan_custom_ignore_patterns(tmp_path: Path) -> None:
    (tmp_path / "skip_me.log").write_text("log")
    (tmp_path / "keep_me.txt").write_text("txt")
    (tmp_path / "other.bak").write_text("bak")

    # Via settings.ignore
    settings = Settings(ignore=["*.log"])
    infos = list(scan(tmp_path, settings=settings, ignore_patterns=["*.bak"]))
    names = [i.name for i in infos]
    assert names == ["keep_me.txt"]


def test_scan_skip_hidden(tmp_path: Path) -> None:
    (tmp_path / ".dotfile").write_text("secret")
    hidden_dir = tmp_path / ".dotdir"
    hidden_dir.mkdir()
    (hidden_dir / "child.txt").write_text("child")
    (tmp_path / "visible.txt").write_text("visible")

    # With skip_hidden = True (default)
    infos = list(scan(tmp_path, recursive=True, settings=Settings(skip_hidden=True)))
    names = [i.name for i in infos]
    assert names == ["visible.txt"]

    # With skip_hidden = False
    infos_all = list(scan(tmp_path, recursive=True, settings=Settings(skip_hidden=False)))
    names_all = {i.name for i in infos_all}
    assert ".dotfile" in names_all
    assert "child.txt" in names_all
    assert "visible.txt" in names_all


def test_scan_symlinks_default_refusal(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    # follow_symlinks = False: is_symlink=True, is_regular=False
    infos = list(scan(tmp_path, settings=Settings(follow_symlinks=False)))
    link_info = next(i for i in infos if i.name == "link.txt")
    assert link_info.is_symlink is True
    assert link_info.is_regular is False


def test_scan_symlinks_follow(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target content")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    # follow_symlinks = True: is_symlink=True, is_regular=True
    infos = list(scan(tmp_path, settings=Settings(follow_symlinks=True)))
    link_info = next(i for i in infos if i.name == "link.txt")
    assert link_info.is_symlink is True
    assert link_info.is_regular is True
    assert link_info.size == len("target content")


def test_scan_broken_symlink(tmp_path: Path) -> None:
    link = tmp_path / "broken_link.txt"
    link.symlink_to(tmp_path / "nonexistent.txt")

    infos = list(scan(tmp_path, settings=Settings(follow_symlinks=True)))
    link_info = next(i for i in infos if i.name == "broken_link.txt")
    assert link_info.is_symlink is True
    assert link_info.is_regular is False


def test_scan_symlinked_directory_never_descended(tmp_path: Path) -> None:
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "inner.txt").write_text("inner")

    link_dir = tmp_path / "link_dir"
    link_dir.symlink_to(real_dir)

    # In recursive mode, link_dir is yielded with is_regular=False and never descended
    infos = list(scan(tmp_path, recursive=True, settings=Settings(follow_symlinks=True)))
    names = {i.name for i in infos}
    assert "inner.txt" in names  # from real_dir
    assert "link_dir" in names  # symlinked dir itself yielded as non-regular
    link_dir_info = next(i for i in infos if i.name == "link_dir")
    assert link_dir_info.is_regular is False
    assert link_dir_info.is_symlink is True


def test_scan_non_recursive_yields_dir_as_non_regular(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inner.txt").write_text("inner")
    (tmp_path / "root.txt").write_text("root")

    infos = list(scan(tmp_path, recursive=False))
    names = {i.name for i in infos}
    assert names == {"root.txt", "sub"}

    sub_info = next(i for i in infos if i.name == "sub")
    assert sub_info.is_regular is False


def test_scan_recursive_descends(tmp_path: Path) -> None:
    sub = tmp_path / "sub" / "deep"
    sub.mkdir(parents=True)
    (sub / "deep_file.txt").write_text("deep")
    (tmp_path / "top_file.txt").write_text("top")

    infos = list(scan(tmp_path, recursive=True))
    names = {i.name for i in infos}
    assert names == {"top_file.txt", "deep_file.txt"}


def test_scan_nonexistent_or_file(tmp_path: Path) -> None:
    assert list(scan(tmp_path / "missing")) == []

    a_file = tmp_path / "file.txt"
    a_file.write_text("hello")
    assert list(scan(a_file)) == []


def test_scan_one_lstat_per_yielded_entry(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "f1.txt").write_text("1")
    (tmp_path / "f2.txt").write_text("2")
    (tmp_path / "skip.tmp").write_text("3")  # ignored

    original_lstat = Path.lstat
    lstat_count = 0

    def counting_lstat(self: Path) -> os.stat_result:
        nonlocal lstat_count
        lstat_count += 1
        return original_lstat(self)

    monkeypatch.setattr(Path, "lstat", counting_lstat)

    infos = list(scan(tmp_path))
    assert len(infos) == 2
    # At most 1 lstat per yielded entry
    assert lstat_count == 2


def test_scan_case_sensitive(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("lower")
    (tmp_path / "file.TXT").write_text("upper")

    # Case-sensitive ignore of uppercase only
    settings = Settings(case_sensitive=True, ignore=["*.TXT"])
    infos = list(scan(tmp_path, settings=settings))
    assert [i.name for i in infos] == ["file.txt"]

    # Case-insensitive ignore matches both
    settings_ci = Settings(case_sensitive=False, ignore=["*.TXT"])
    infos_ci = list(scan(tmp_path, settings=settings_ci))
    assert [i.name for i in infos_ci] == []


def test_scan_scandir_oserror(tmp_path: Path, monkeypatch) -> None:
    def failing_scandir(path):
        raise OSError("Permission denied")

    monkeypatch.setattr(os, "scandir", failing_scandir)
    assert list(scan(tmp_path)) == []


def test_scan_entry_oserror(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "ok.txt").write_text("ok")
    (tmp_path / "bad.txt").write_text("bad")

    orig_from_path = FileInfo.from_path

    def flaky_from_path(path):
        if "bad" in str(path):
            raise OSError("I/O error")
        return orig_from_path(path)

    monkeypatch.setattr(FileInfo, "from_path", classmethod(lambda cls, p: flaky_from_path(p)))

    infos = list(scan(tmp_path))
    assert len(infos) == 1
    assert infos[0].name == "ok.txt"
