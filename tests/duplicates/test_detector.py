"""Tests for DuplicateDetectorImpl.

Covers acceptance criteria A53-A60 and safety invariants S6, S7.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

import clutterxp.duplicates.detector as detector_mod
from clutterxp.duplicates.detector import DuplicateDetectorImpl
from clutterxp.duplicates.hashcache import HashCache
from clutterxp.duplicates.report import groups_to_csv_rows, groups_to_json
from clutterxp.interfaces import DuplicateDetector


def test_conforms_to_protocol() -> None:
    """DuplicateDetectorImpl satisfies the runtime-checkable DuplicateDetector protocol."""
    detector = DuplicateDetectorImpl()
    assert isinstance(detector, DuplicateDetector)


def test_A53_identical_files_reported_exact(tmp_path: Path) -> None:
    """A53: Byte-identical files are reported as exact groups with matching SHA-256."""
    dir_a = tmp_path / "folder_a"
    dir_b = tmp_path / "folder_b"
    dir_a.mkdir()
    dir_b.mkdir()

    content = b"Exact duplicate content across directories."
    file_a = dir_a / "document.pdf"
    file_b = dir_b / "doc_copy.pdf"
    file_a.write_bytes(content)
    file_b.write_bytes(content)

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path])

    assert len(groups) == 1
    g = groups[0]
    assert g.kind == "exact"
    assert len(g.files) == 2
    assert g.sha256 == hashlib.sha256(content).hexdigest()
    assert g.wasted_bytes == len(content)
    assert {f.path for f in g.files} == {file_a, file_b}


def test_A54_same_size_different_content_not_exact(tmp_path: Path) -> None:
    """A54: Same size with different content is not reported as exact."""
    file_a = tmp_path / "file1.bin"
    file_b = tmp_path / "file2.bin"

    file_a.write_bytes(b"1234567890ABCDEF")
    file_b.write_bytes(b"FEDCBA0987654321")
    assert file_a.stat().st_size == file_b.stat().st_size

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path], potential=False)

    exact_groups = [g for g in groups if g.kind == "exact"]
    assert len(exact_groups) == 0


def test_A55_equal_head_tail_different_middle_separated_by_full_hash(
    tmp_path: Path,
) -> None:
    """A55: Equal head/tail/size but different middle is separated by full hash."""
    head = b"H" * 65536
    tail = b"T" * 65536
    middle_a = b"AAAA" * 1024
    middle_b = b"BBBB" * 1024

    file_a = tmp_path / "collision1.dat"
    file_b = tmp_path / "collision2.dat"

    file_a.write_bytes(head + middle_a + tail)
    file_b.write_bytes(head + middle_b + tail)
    assert file_a.stat().st_size == file_b.stat().st_size

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path], potential=False)

    exact_groups = [g for g in groups if g.kind == "exact"]
    assert len(exact_groups) == 0


def test_A56_hardlinks_reported_and_excluded_from_wasted(tmp_path: Path) -> None:
    """A56: Hardlinks are reported as hardlink kind and excluded from wasted space."""
    orig = tmp_path / "original.txt"
    orig.write_bytes(b"Shared hardlink inode data.")

    link_path = tmp_path / "link.txt"
    os.link(orig, link_path)

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path])

    hardlink_groups = [g for g in groups if g.kind == "hardlink"]
    exact_groups = [g for g in groups if g.kind == "exact"]

    assert len(hardlink_groups) == 1
    assert len(exact_groups) == 0

    hg = hardlink_groups[0]
    assert hg.kind == "hardlink"
    assert hg.wasted_bytes == 0
    assert hg.sha256 is None
    assert len(hg.files) == 2
    assert {f.path for f in hg.files} == {orig, link_path}


def test_A57_potential_groups_labeled_review_only(tmp_path: Path) -> None:
    """A57: Potential duplicates come from closed name-pattern list and are labeled review-only."""
    file_orig = tmp_path / "report.pdf"
    file_copy = tmp_path / "report (1).pdf"

    file_orig.write_bytes(b"Version A of report")
    file_copy.write_bytes(b"Version B of report with revisions")

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path], potential=True)

    potential_groups = [g for g in groups if g.kind == "potential"]
    exact_groups = [g for g in groups if g.kind == "exact"]

    assert len(exact_groups) == 0
    assert len(potential_groups) == 1

    pg = potential_groups[0]
    assert pg.kind == "potential"
    assert pg.wasted_bytes == 0
    assert pg.sha256 is None
    assert "same normalized name 'report', ext 'pdf', content differs" in pg.note
    assert {f.path for f in pg.files} == {file_orig, file_copy}


def test_A58_repeat_scan_no_full_rehash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A58: Second scan uses cache and performs zero full re-hashes."""
    cache = HashCache(tmp_path / "cache.db")
    detector = DuplicateDetectorImpl(cache=cache)

    user_dir = tmp_path / "user_files"
    user_dir.mkdir()
    f1 = user_dir / "file1.bin"
    f2 = user_dir / "file2.bin"
    content = b"Repeat scan cached hash content test" * 200
    f1.write_bytes(content)
    f2.write_bytes(content)

    full_hash_calls = 0
    orig_full_hash = detector_mod.compute_full_hash

    def spy_full_hash(*args: Any, **kwargs: Any) -> str:
        nonlocal full_hash_calls
        full_hash_calls += 1
        return orig_full_hash(*args, **kwargs)

    monkeypatch.setattr(detector_mod, "compute_full_hash", spy_full_hash)

    # First scan computes full hashes
    groups1 = detector.scan([user_dir])
    assert len(groups1) == 1
    assert full_hash_calls == 2

    # Second scan uses cache: full_hash_calls does not increase
    groups2 = detector.scan([user_dir])
    assert len(groups2) == 1
    assert full_hash_calls == 2
    assert groups1[0].sha256 == groups2[0].sha256

    cache.close()


def test_A59_scan_zero_writes_to_user_folders(
    tmp_path: Path, make_tree: Any, snapshot: Any, assert_unchanged: Any
) -> None:
    """A59: Duplicate detector scan never writes, moves, or deletes user files."""
    user_tree = tmp_path / "user_data"
    make_tree(
        {
            "doc1.txt": "same text content",
            "doc2.txt": "same text content",
            "sub/doc3.txt": "same text content",
            "unique.dat": b"\x01\x02\x03\x04",
        },
        base=user_tree,
    )

    before = snapshot(user_tree)

    cache = HashCache(tmp_path / "cache.db")
    detector = DuplicateDetectorImpl(cache=cache)
    groups = detector.scan([user_tree])
    assert len(groups) >= 1

    assert_unchanged(user_tree, before)
    cache.close()


def test_A60_json_csv_output_works(tmp_path: Path) -> None:
    """A60: Table, JSON and CSV outputs format correctly and contain expected schema."""
    user_dir = tmp_path / "data"
    user_dir.mkdir()

    # Exact duplicates
    content = b"Exact test file content"
    (user_dir / "a.txt").write_bytes(content)
    (user_dir / "b.txt").write_bytes(content)

    # Hardlink
    (user_dir / "hard1.dat").write_bytes(b"Hardlink content")
    os.link(user_dir / "hard1.dat", user_dir / "hard2.dat")

    # Potential
    (user_dir / "notes.txt").write_bytes(b"Notes v1")
    (user_dir / "notes (1).txt").write_bytes(b"Notes v2 with different edits")

    detector = DuplicateDetectorImpl()
    groups = detector.scan([user_dir])

    # 1. JSON output
    data = groups_to_json(
        groups, [user_dir], detector.last_scanned_files, detector.last_scanned_bytes
    )
    assert data["schema_version"] == 1
    assert data["scanned"]["files"] == detector.last_scanned_files
    assert data["scanned"]["bytes"] == detector.last_scanned_bytes
    assert len(data["exact"]) == 1
    assert len(data["hardlinks"]) == 1
    assert len(data["potential"]) == 1
    assert data["summary"]["exact_groups"] == 1
    assert data["summary"]["wasted_bytes"] == len(content)
    assert data["summary"]["potential_groups"] == 1

    # Verify JSON serializability
    json_str = json.dumps(data)
    assert "schema_version" in json_str

    # 2. CSV output
    csv_rows = groups_to_csv_rows(groups)
    assert csv_rows[0] == [
        "group_id",
        "kind",
        "sha256",
        "size",
        "wasted_bytes",
        "note",
        "path",
        "mtime_ns",
        "inode",
    ]
    # Header + data rows
    assert len(csv_rows) > 1
    for row in csv_rows:
        for cell in row:
            assert isinstance(cell, str)

    # Verify CSV writer formatting
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(csv_rows)
    assert "exact" in buf.getvalue()


def test_file_modified_during_scan_skipped_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-stat after hashing catches file modified during scan and skips with warning."""
    f1 = tmp_path / "f1.bin"
    f2 = tmp_path / "f2.bin"
    f1.write_bytes(b"Original size content 12345")
    f2.write_bytes(b"Original size content 12345")

    orig_full_hash = detector_mod.compute_full_hash

    def mutating_full_hash(path: Path, size: int, *args: Any, **kwargs: Any) -> str:
        if path == f1:
            # Modify the file on disk during hashing
            path.write_bytes(b"Modified content during hash!")
        return orig_full_hash(path, size, *args, **kwargs)

    monkeypatch.setattr(detector_mod, "compute_full_hash", mutating_full_hash)

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path], potential=False)

    # File 1 was skipped due to stat mismatch, so no exact group formed
    exact_groups = [g for g in groups if g.kind == "exact"]
    assert len(exact_groups) == 0


def test_unreadable_file_continues_scan_S15(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant S15: Unreadable file logs warning and does not abort remaining scan."""
    f1 = tmp_path / "file1.txt"
    f2 = tmp_path / "file2.txt"
    f3 = tmp_path / "file3.txt"
    content = b"All three have identical bytes"
    f1.write_bytes(content)
    f2.write_bytes(content)
    f3.write_bytes(content)

    real_open = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "file2.txt":
            raise PermissionError("Permission denied for testing")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path], potential=False)

    # Scan continued and found f1 and f3 as duplicates
    exact_groups = [g for g in groups if g.kind == "exact"]
    assert len(exact_groups) == 1
    assert {f.path for f in exact_groups[0].files} == {f1, f3}


def test_cancel_via_threading_event(tmp_path: Path) -> None:
    """Scan cleanly cancels when threading.Event is set."""
    f1 = tmp_path / "f1.txt"
    f2 = tmp_path / "f2.txt"
    f1.write_bytes(b"content")
    f2.write_bytes(b"content")

    cancel_event = threading.Event()
    cancel_event.set()

    detector = DuplicateDetectorImpl(cancel=cancel_event)
    groups = detector.scan([tmp_path])
    assert groups == []


def test_symlinks_skipped_by_default(tmp_path: Path) -> None:
    """Symlinks are skipped by default in stage 0."""
    real_file = tmp_path / "real.txt"
    real_file.write_bytes(b"Real file content")

    symlink_file = tmp_path / "symlink.txt"
    symlink_file.symlink_to(real_file)

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path])
    assert len(groups) == 0


def test_S6_duplicates_scan_zero_writes(
    tmp_path: Path, make_tree: Any, snapshot: Any, assert_unchanged: Any
) -> None:
    """Safety S6: duplicates scan performs zero writes to user folders."""
    user_tree = tmp_path / "user_data"
    make_tree(
        {
            "a.pdf": "invoice a",
            "b.pdf": "invoice a",
            "c.pdf": "different invoice",
        },
        base=user_tree,
    )
    before = snapshot(user_tree)

    detector = DuplicateDetectorImpl()
    detector.scan([user_tree])

    assert_unchanged(user_tree, before)


def test_S7_duplicates_scan_read_only(
    tmp_path: Path, make_tree: Any, snapshot: Any, assert_unchanged: Any
) -> None:
    """Safety S7: duplicate detector is strictly read-only on inspected directories."""
    user_tree = tmp_path / "read_only_tree"
    make_tree(
        {
            "img1.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 50,
            "img2.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 50,
        },
        base=user_tree,
    )
    before = snapshot(user_tree)

    detector = DuplicateDetectorImpl()
    detector.scan([user_tree], jobs=2)

    assert_unchanged(user_tree, before)


def test_progress_callback(tmp_path: Path) -> None:
    """Progress callback receives bytes_done and bytes_total during full hashing."""
    f1 = tmp_path / "file1.bin"
    f2 = tmp_path / "file2.bin"
    data = b"Progress testing content bytes!" * 100
    f1.write_bytes(data)
    f2.write_bytes(data)

    progress_calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        progress_calls.append((done, total))

    detector = DuplicateDetectorImpl()
    detector.scan([tmp_path], progress=on_progress)

    assert len(progress_calls) > 0
    final_done, final_total = progress_calls[-1]
    assert final_done == final_total
    assert final_total == len(data) * 2


def test_exclude_and_min_size(tmp_path: Path) -> None:
    """Exclude patterns and min_size filter out files."""
    (tmp_path / "small.txt").write_bytes(b"tiny")
    (tmp_path / "excluded.log").write_bytes(b"log file content here")
    (tmp_path / "valid1.txt").write_bytes(b"large enough content here")
    (tmp_path / "valid2.txt").write_bytes(b"large enough content here")

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path], min_size=10, exclude=["*.log"])

    exact = [g for g in groups if g.kind == "exact"]
    assert len(exact) == 1
    assert {f.name for f in exact[0].files} == {"valid1.txt", "valid2.txt"}


def test_nonexistent_folder_skips_cleanly(tmp_path: Path) -> None:
    """Non-existent folders or files in folders argument are skipped with a warning."""
    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path / "does_not_exist", tmp_path / "also_missing"])
    assert groups == []


def test_vanished_file_after_hashing_handled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File vanishing before re-stat after hashing is logged and skipped."""
    f1 = tmp_path / "f1.txt"
    f2 = tmp_path / "f2.txt"
    f1.write_bytes(b"content")
    f2.write_bytes(b"content")

    real_lstat = Path.lstat

    def vanishing_lstat(self: Path) -> Any:
        if self == f1:
            raise FileNotFoundError("Vanished during hashing")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", vanishing_lstat)

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path], potential=False)
    assert groups == []


def test_seen_paths_deduplicated_across_folders(tmp_path: Path) -> None:
    """Scanning identical or overlapping folder paths does not duplicate files."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_bytes(b"duplicate across overlap")
    f2.write_bytes(b"duplicate across overlap")

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path, tmp_path])
    assert len(groups) == 1
    assert len(groups[0].files) == 2


def test_directory_walk_oserror_handled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Directory walk OSError (e.g. unreadable directory) logs warning and continues."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "file.txt").write_bytes(b"content")

    real_scandir = os.scandir

    def failing_scandir(path: Any) -> Any:
        if Path(path) == sub:
            raise PermissionError("Cannot read subfolder")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)

    detector = DuplicateDetectorImpl()
    groups = detector.scan([tmp_path])
    assert groups == []


def test_cancel_during_compute_full_hash(tmp_path: Path) -> None:
    """Cancel event triggers InterruptedError during streaming hash and aborts cleanly."""
    f1 = tmp_path / "f1.bin"
    f2 = tmp_path / "f2.bin"
    f1.write_bytes(b"abc" * 1000)
    f2.write_bytes(b"abc" * 1000)

    cancel = threading.Event()
    cancel.set()

    with pytest.raises(InterruptedError):
        detector_mod.compute_full_hash(f1, 3000, cancel=cancel)
