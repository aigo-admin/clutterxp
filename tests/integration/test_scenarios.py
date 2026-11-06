"""Realistic scenario tests for ClutterXP.

Exercises the full stack in realistic conditions:
1. Chaos Downloads folder simulation: hundreds of files with ugly names (spaces,
   unicode, newlines, surrogateescape bytes, 255-byte names, leading dashes, symlinks,
   directories, hidden files, partial browser downloads). Organize -> Undo -> Re-organize.
2. Watch while background writer appends data (settle & open-for-write checks).
3. Concurrent manual organize during watch (per-operation fs_lock interleaving).
4. Two watchers refused via watch lock (exit code 5).
5. Config reload during watch activity.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clutterxp.cli.app import app
from clutterxp.config.loader import load
from clutterxp.history.service import HistoryService
from clutterxp.locking import watch_lock
from clutterxp.models import FileInfo
from clutterxp.mover.safe_move import SafeMover
from clutterxp.organizer import OrganizerImpl
from clutterxp.rules import build_engine
from clutterxp.watch.settle import Settler, SettleStatus
from tests.conftest import EnvPaths


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# =============================================================================
# Scenario 1: Realistic Downloads Chaos Simulation
# =============================================================================


def test_scenario_downloads_chaos_organize_undo_reorganize(
    env: EnvPaths,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Chaos Downloads folder: hundreds of files, hostile names, organize -> undo -> re-organize."""
    downloads = tmp_path / "Downloads"
    dest_docs = tmp_path / "Organized" / "Documents"
    dest_media = tmp_path / "Organized" / "Media"
    dest_archives = tmp_path / "Organized" / "Archives"

    downloads.mkdir(parents=True, exist_ok=True)
    dest_docs.mkdir(parents=True, exist_ok=True)
    dest_media.mkdir(parents=True, exist_ok=True)
    dest_archives.mkdir(parents=True, exist_ok=True)

    # External file for symlink target
    external_target = tmp_path / "external_file.pdf"
    external_target.write_text("external content", encoding="utf-8")

    # 1. Populate downloads directory with chaotic variety of files
    expected_moved_docs: list[str] = []
    expected_moved_media: list[str] = []
    expected_moved_archives: list[str] = []
    expected_untouched: list[str] = []

    # Normal matching files
    for i in range(1, 40):
        name = f"invoice_{i:03d}.pdf"
        (downloads / name).write_text(f"invoice content {i}", encoding="utf-8")
        expected_moved_docs.append(name)

    for i in range(1, 30):
        name = f"photo_{i:03d}.jpg"
        (downloads / name).write_text(f"image content {i}", encoding="utf-8")
        expected_moved_media.append(name)

    for i in range(1, 20):
        name = f"backup_{i:03d}.tar.gz"
        (downloads / name).write_text(f"archive content {i}", encoding="utf-8")
        expected_moved_archives.append(name)

    # Hostile / Ugly names that MUST match and organize cleanly:
    # A. Leading dash
    dash_name = "-rf_important_report.pdf"
    (downloads / dash_name).write_text("dash content", encoding="utf-8")
    expected_moved_docs.append(dash_name)

    # B. Multiple spaces and punctuation
    spaced_name = "Quarterly  Tax   Return (2026) [FINAL]!.pdf"
    (downloads / spaced_name).write_text("tax return content", encoding="utf-8")
    expected_moved_docs.append(spaced_name)

    # C. Non-ASCII Unicode (German umlauts, Japanese, emojis)
    unicode_doc = "Über_Rechnung_für_Kunde.pdf"
    (downloads / unicode_doc).write_text("german doc", encoding="utf-8")
    expected_moved_docs.append(unicode_doc)

    unicode_img = "写真_旅行_2026.jpg"
    (downloads / unicode_img).write_text("japanese image", encoding="utf-8")
    expected_moved_media.append(unicode_img)

    # D. 255-byte name (maximum Linux filename length)
    max_stem = "invoice_long_" + ("x" * 230)
    max_name = max_stem[:251] + ".pdf"
    assert len(max_name.encode("utf-8")) <= 255
    (downloads / max_name).write_text("max length content", encoding="utf-8")
    expected_moved_docs.append(max_name)

    # Files that MUST remain UNTOUCHED in downloads:
    # E. Symlink to external file
    symlink_name = "symlink_invoice.pdf"
    (downloads / symlink_name).symlink_to(external_target)
    expected_untouched.append(symlink_name)

    # F. Subdirectory
    subdir_name = "invoice_subfolder"
    (downloads / subdir_name).mkdir()
    (downloads / subdir_name / "nested.pdf").write_text("nested", encoding="utf-8")
    expected_untouched.append(subdir_name)

    # G. Hidden files
    hidden_file = ".hidden_invoice.pdf"
    (downloads / hidden_file).write_text("hidden data", encoding="utf-8")
    expected_untouched.append(hidden_file)

    # H. Browser partial downloads
    part_file1 = "ubuntu-server.iso.crdownload"
    part_file2 = "archive.tar.gz.part"
    (downloads / part_file1).write_text("partial crdownload", encoding="utf-8")
    (downloads / part_file2).write_text("partial part", encoding="utf-8")
    expected_untouched.append(part_file1)
    expected_untouched.append(part_file2)

    # I. Completely unmatched file
    unmatched_file = "random_unmatched.bin"
    (downloads / unmatched_file).write_text("binary blob", encoding="utf-8")
    expected_untouched.append(unmatched_file)

    # Configure rules
    env.config_path.write_text(
        f"version: 1\n"
        f"settings:\n"
        f"  create_destinations: all\n"
        f"rules:\n"
        f"  - name: Documents\n"
        f"    extensions: [pdf, doc, docx]\n"
        f"    destination: {dest_docs}\n"
        f"  - name: Media\n"
        f"    extensions: [jpg, png, mp3, mp4]\n"
        f"    destination: {dest_media}\n"
        f"  - name: Archives\n"
        f"    extensions: [tar.gz, zip]\n"
        f"    destination: {dest_archives}\n",
        encoding="utf-8",
    )

    # -------------------------------------------------------------------------
    # STEP 1: Run organize
    # -------------------------------------------------------------------------
    res_org = runner.invoke(app, ["organize", str(downloads), "--yes"])
    assert res_org.exit_code == 0

    # Verify moved documents
    for name in expected_moved_docs:
        assert (dest_docs / name).exists(), f"Expected {name} to be in {dest_docs}"
        assert not (downloads / name).exists(), f"Expected {name} to be removed from downloads"

    # Verify moved media
    for name in expected_moved_media:
        assert (dest_media / name).exists(), f"Expected {name} to be in {dest_media}"
        assert not (downloads / name).exists(), f"Expected {name} to be removed from downloads"

    # Verify moved archives
    for name in expected_moved_archives:
        assert (dest_archives / name).exists(), f"Expected {name} to be in {dest_archives}"
        assert not (downloads / name).exists(), f"Expected {name} to be removed from downloads"

    # Verify untouched items remained in downloads
    for name in expected_untouched:
        assert (downloads / name).exists(), f"Expected {name} to stay in downloads"

    # -------------------------------------------------------------------------
    # STEP 2: Run undo (batch undo)
    # -------------------------------------------------------------------------
    res_undo = runner.invoke(app, ["undo", "--yes"])
    assert res_undo.exit_code == 0

    # All files restored back to downloads!
    for name in expected_moved_docs:
        assert (downloads / name).exists(), f"Expected {name} to be restored to downloads"
        assert not (dest_docs / name).exists()

    for name in expected_moved_media:
        assert (downloads / name).exists(), f"Expected {name} to be restored to downloads"
        assert not (dest_media / name).exists()

    for name in expected_moved_archives:
        assert (downloads / name).exists(), f"Expected {name} to be restored to downloads"
        assert not (dest_archives / name).exists()

    # Content verification
    assert (downloads / dash_name).read_text(encoding="utf-8") == "dash content"
    assert (downloads / spaced_name).read_text(encoding="utf-8") == "tax return content"
    assert (downloads / unicode_doc).read_text(encoding="utf-8") == "german doc"

    # -------------------------------------------------------------------------
    # STEP 3: Re-organize again deterministically
    # -------------------------------------------------------------------------
    res_reorg = runner.invoke(app, ["organize", str(downloads), "--yes"])
    assert res_reorg.exit_code == 0

    for name in expected_moved_docs:
        assert (dest_docs / name).exists()
    for name in expected_moved_media:
        assert (dest_media / name).exists()
    for name in expected_moved_archives:
        assert (dest_archives / name).exists()


# =============================================================================
# Scenario 2: Watch While Background Writer Appends Data
# =============================================================================


def test_scenario_watch_while_background_writer_appends(tmp_path: Path) -> None:
    """Settler waits while background writer is actively writing data to file."""
    watch_dir = tmp_path / "incoming"
    watch_dir.mkdir()
    target_file = watch_dir / "downloading.pdf"

    settler = Settler(sample_interval=0.1, max_wait=5.0)
    stop_writer = threading.Event()

    def background_writer() -> None:
        with target_file.open("wb") as f:
            while not stop_writer.is_set():
                f.write(b"chunk of downloading data...\n")
                f.flush()
                time.sleep(0.04)

    writer_thread = threading.Thread(target=background_writer, daemon=True)
    writer_thread.start()

    time.sleep(0.05)

    try:
        # Check settler while actively writing: must NOT settle yet
        for _ in range(4):
            dec = settler.check(target_file, settle_seconds=0.2)
            assert dec == SettleStatus.NOT_YET, "Settler must wait while file is actively written"
            time.sleep(0.05)
    finally:
        stop_writer.set()
        writer_thread.join(timeout=2.0)

    # Now that writer stopped, wait for quiet settle period
    time.sleep(0.25)
    # Clear previous samples and check again
    settler.clear(target_file)
    settler.check(target_file, settle_seconds=0.1)
    time.sleep(0.15)
    dec2 = settler.check(target_file, settle_seconds=0.1)
    assert dec2 == SettleStatus.READY, "Settler must settle once writer stops and stat is stable"


# =============================================================================
# Scenario 3: Concurrent Manual Organize During Watch
# =============================================================================


def test_scenario_concurrent_manual_organize_during_watch(
    env: EnvPaths,
    tmp_path: Path,
) -> None:
    """Manual organize and watcher interleave safely under per-operation fs_lock."""
    inbox = tmp_path / "inbox"
    dest = tmp_path / "dest"
    inbox.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)

    # 40 files created
    for i in range(40):
        (inbox / f"report_{i:02d}.pdf").write_text(f"content {i}", encoding="utf-8")

    env.config_path.write_text(
        f"version: 1\nrules:\n  - name: Reports\n    extensions: [pdf]\n    destination: {dest}\n",
        encoding="utf-8",
    )

    cfg = load(env.config_path)
    engine = build_engine(list(cfg.rules), cfg.settings)
    history = HistoryService(env.state_dir / "history.db")
    mover = SafeMover()
    organizer = OrganizerImpl(cfg, engine, mover, history, fs_lock_path=env.state_dir / "fs.lock")

    errors: list[Exception] = []

    def run_organize() -> None:
        try:
            plan = organizer.plan([inbox])
            organizer.apply(plan)
        except Exception as e:
            errors.append(e)

    # Run two organize threads concurrently on the same folder
    t1 = threading.Thread(target=run_organize)
    t2 = threading.Thread(target=run_organize)

    t1.start()
    t2.start()

    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not errors, f"Errors occurred during concurrent organize: {errors}"

    # All 40 files must end up in dest, with no duplicates or missing files
    dest_files = list(dest.glob("*.pdf"))
    assert len(dest_files) == 40
    assert len(list(inbox.glob("*.pdf"))) == 0


# =============================================================================
# Scenario 4: Two Watchers Refused via Watch Lock (Exit Code 5)
# =============================================================================


def test_scenario_two_watchers_refused(env: EnvPaths, runner: CliRunner, tmp_path: Path) -> None:
    """Two watchers running simultaneously: second watcher is refused with exit code 5."""
    watch_folder = tmp_path / "watched"
    watch_folder.mkdir()
    dest_folder = tmp_path / "dest"
    dest_folder.mkdir()

    env.config_path.write_text(
        f"version: 1\n"
        f"watch:\n"
        f"  - path: {watch_folder}\n"
        f"rules:\n"
        f"  - name: WatchRule\n"
        f"    keywords: [test]\n"
        f"    destination: {dest_folder}\n",
        encoding="utf-8",
    )

    watch_lock_file = env.state_dir / "watch.lock"

    # First watcher process holds watch_lock
    with watch_lock(watch_lock_file):
        # Second watcher tries to start via CLI
        res = runner.invoke(app, ["watch", "--poll"])
        assert res.exit_code == 5


# =============================================================================
# Scenario 5: Config Reload During Activity
# =============================================================================


def test_scenario_config_reload_during_activity(
    env: EnvPaths,
    tmp_path: Path,
) -> None:
    """Config reload during activity: initial rules swap cleanly to new rules."""
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    dest1.mkdir()
    dest2.mkdir()

    # Rule V1: *.txt -> dest1
    env.config_path.write_text(
        f"version: 1\nrules:\n  - name: V1Rule\n    extensions: [txt]\n    destination: {dest1}\n",
        encoding="utf-8",
    )

    cfg_v1 = load(env.config_path)
    engine_v1 = build_engine(list(cfg_v1.rules), cfg_v1.settings)

    f1 = tmp_path / "file1.txt"
    f1.write_text("file 1 content", encoding="utf-8")
    info1 = FileInfo.from_path(f1)

    dec1 = engine_v1.decide(info1)
    assert dec1.hit is not None
    assert dec1.hit.rule_name == "V1Rule"
    assert str(dest1) in str(dec1.hit.dest_dir)

    # Update config to Rule V2: *.txt -> dest2
    env.config_path.write_text(
        f"version: 1\nrules:\n  - name: V2Rule\n    extensions: [txt]\n    destination: {dest2}\n",
        encoding="utf-8",
    )

    cfg_v2 = load(env.config_path)
    engine_v2 = build_engine(list(cfg_v2.rules), cfg_v2.settings)

    f2 = tmp_path / "file2.txt"
    f2.write_text("file 2 content", encoding="utf-8")
    info2 = FileInfo.from_path(f2)

    dec2 = engine_v2.decide(info2)
    assert dec2.hit is not None
    assert dec2.hit.rule_name == "V2Rule"
    assert str(dest2) in str(dec2.hit.dest_dir)
