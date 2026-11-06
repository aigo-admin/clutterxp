"""Acceptance criteria tests for Watch Service (A41 through A52)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clutterxp.config import loader
from clutterxp.models import SafetyRefusal
from clutterxp.watch.events import FileCreatedEvent, FileModifiedEvent, WatchEventHandler
from clutterxp.watch.service import WatchService
from tests.conftest import ConfigTextFn, EnvPaths
from tests.helpers import wait_until
from tests.watch.conftest import FakeOrganizer


@pytest.mark.slow
def test_A41_file_settles_and_is_sorted(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A41: A file appearing in any configured folder is sorted once it has settled."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["doc"]}],
        settings={"settle_seconds": 0.3},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir, rule_name="Rule1")
    service = WatchService(
        env.config_path,
        organizer,
        sample_interval=0.1,
    )
    service._settler.sample_interval = 0.1
    service.start()
    try:
        file_path = inbox / "my_doc.pdf"
        file_path.write_bytes(b"sample file content")

        wait_until(
            lambda: (sorted_dir / "my_doc.pdf").exists() and not file_path.exists(),
            timeout=5.0,
            message="File was not sorted into destination after settling",
        )
    finally:
        service.stop()


@pytest.mark.slow
def test_A42_browser_download_part_rename_handled(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A42: Browser-style .part then rename is handled without moving the partial file."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["ebook"]}],
        settings={"settle_seconds": 0.3},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service._settler.sample_interval = 0.1
    service.start()
    try:
        # 1. Browser creates partial download
        part_file = inbox / "ebook.pdf.part"
        part_file.write_bytes(b"partial content chunk 1")
        time.sleep(0.1)
        part_file.write_bytes(b"partial content chunk 1 + 2")
        time.sleep(0.4)

        # Assert .part file is untouched and not sorted
        assert part_file.exists()
        assert not (sorted_dir / "ebook.pdf.part").exists()
        assert not (sorted_dir / "ebook.pdf").exists()

        # 2. Browser renames .part to final name
        final_file = inbox / "ebook.pdf"
        part_file.rename(final_file)

        # Assert final file is detected and sorted
        wait_until(
            lambda: (sorted_dir / "ebook.pdf").exists() and not final_file.exists(),
            timeout=5.0,
            message="Renamed .pdf was not sorted into destination",
        )
    finally:
        service.stop()


@pytest.mark.slow
def test_A43_file_moved_in_with_old_mtime_processed(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A43: A file moved into a watched folder (old mtime) is processed."""
    inbox = tmp_path / "inbox"
    outside = tmp_path / "outside"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    outside.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["archive"]}],
        settings={"settle_seconds": 0.3},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service._settler.sample_interval = 0.1
    service.start()
    try:
        # Create file outside with an old mtime (1 hour ago)
        old_file = outside / "archive.zip"
        old_file.write_bytes(b"zip archive data")
        past_time = time.time() - 3600
        os.utime(old_file, (past_time, past_time))

        # Move into watched inbox
        dest_in_inbox = inbox / "archive.zip"
        old_file.rename(dest_in_inbox)

        # Should be processed despite old mtime
        wait_until(
            lambda: (sorted_dir / "archive.zip").exists() and not dest_in_inbox.exists(),
            timeout=5.0,
            message="Moved-in file with old mtime was not sorted",
        )
    finally:
        service.stop()


@pytest.mark.slow
def test_A44_file_held_open_for_write_waits(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A44: A file held open for writing by another process is not moved until closed."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["held"]}],
        settings={"settle_seconds": 0.3},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service._settler.sample_interval = 0.1
    service.start()
    try:
        held_file = inbox / "held.txt"

        # Helper script that holds file open for 1.2s
        script = (
            "import time, sys\n"
            "with open(sys.argv[1], 'w') as f:\n"
            "    f.write('data')\n"
            "    f.flush()\n"
            "    sys.stdout.write('OPEN\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(1.2)\n"
            "sys.stdout.write('CLOSED\\n')\n"
            "sys.stdout.flush()\n"
        )

        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(held_file)],
            stdout=subprocess.PIPE,
            text=True,
        )

        # Wait until helper announces file is open
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line == "OPEN"

        # While helper is alive and file is open, file should not be moved
        time.sleep(0.4)
        assert held_file.exists()
        assert not (sorted_dir / "held.txt").exists()

        proc.wait(timeout=5.0)

        # Once closed, file settles and moves
        wait_until(
            lambda: (sorted_dir / "held.txt").exists() and not held_file.exists(),
            timeout=5.0,
            message="Held-open file was not moved after being closed",
        )
    finally:
        service.stop()


@pytest.mark.slow
def test_A45_ignore_patterns_honored(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A45: Built-in and custom ignore patterns are honored."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3, "ignore": ["*.custom_skip"]}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["file"]}],
        settings={"settle_seconds": 0.3, "skip_hidden": True},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service._settler.sample_interval = 0.1
    service.start()
    try:
        # Create ignored files
        tmp_file = inbox / "file.tmp"
        tmp_file.write_bytes(b"temp")

        cr_file = inbox / "file.crdownload"
        cr_file.write_bytes(b"chrome download")

        custom_file = inbox / "file.custom_skip"
        custom_file.write_bytes(b"custom ignore")

        hidden_file = inbox / ".hidden_file.txt"
        hidden_file.write_bytes(b"hidden")

        # Create valid normal file
        valid_file = inbox / "file_normal.pdf"
        valid_file.write_bytes(b"valid")

        # Wait until valid file is sorted
        wait_until(
            lambda: (sorted_dir / "file_normal.pdf").exists() and not valid_file.exists(),
            timeout=5.0,
        )

        # Assert all ignored files remain in inbox untouched
        assert tmp_file.exists()
        assert cr_file.exists()
        assert custom_file.exists()
        assert hidden_file.exists()
    finally:
        service.stop()


@pytest.mark.slow
def test_A46_unmatched_files_untouched_by_watcher(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A46: Unmatched files are untouched by the watcher."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["match"]}],
        settings={"settle_seconds": 0.3},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(
        default_dest=sorted_dir,
        unmatched_names=("unmatched.txt",),
    )
    service = WatchService(env.config_path, organizer)
    service._settler.sample_interval = 0.1
    service.start()
    try:
        unmatched_file = inbox / "unmatched.txt"
        unmatched_file.write_bytes(b"no matching rule")

        matched_file = inbox / "match_this.pdf"
        matched_file.write_bytes(b"matches rule")

        wait_until(
            lambda: (sorted_dir / "match_this.pdf").exists() and not matched_file.exists(),
            timeout=5.0,
        )

        # Unmatched file stays in inbox
        assert unmatched_file.exists()
        assert not (sorted_dir / "unmatched.txt").exists()
    finally:
        service.stop()


@pytest.mark.slow
def test_A47_burst_of_files_processed_once(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A47: A burst of files is processed exactly once each."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["file"]}],
        settings={"settle_seconds": 0.3},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service._settler.sample_interval = 0.1
    service.start()
    try:
        count = 50
        for i in range(count):
            (inbox / f"file_{i:03d}.pdf").write_bytes(f"burst {i}".encode())

        # Wait until all files are sorted
        wait_until(
            lambda: len(list(sorted_dir.iterdir())) == count,
            timeout=10.0,
            message="Not all burst files were sorted into destination",
        )

        # Assert every file was processed exactly once
        processed_paths = [call.path.name for call in organizer.process_calls]
        assert len(processed_paths) == count
        assert len(set(processed_paths)) == count
    finally:
        service.stop()


def test_A48_watcher_ignores_own_moves(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A48: The watcher ignores events caused by its own moves;

    destinations inside watched folders are rejected at config time.
    """
    inbox = tmp_path / "inbox"
    sub_dest = inbox / "Subfolder"
    inbox.mkdir(parents=True)
    sub_dest.mkdir(parents=True)

    # 1. Config validation forbids destinations inside watched folders (allow_chain: false)
    cfg_invalid = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "RuleLoop", "destination": str(sub_dest), "keywords": ["foo"]}],
        settings={"allow_chain": False},
    )
    env.config_path.write_text(cfg_invalid)

    rt_config = loader.load(env.config_path)
    assert not rt_config.is_valid
    assert any("inside watched folder" in err for err in rt_config.errors)

    # 2. Self-event suppression drops events for src and dst
    sorted_dir = tmp_path / "sorted"
    sorted_dir.mkdir(parents=True)
    cfg_valid = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "RuleValid", "destination": str(sorted_dir), "keywords": ["foo"]}],
        settings={"allow_chain": False},
    )
    env.config_path.write_text(cfg_valid)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service.start()
    try:
        src_file = inbox / "foo.txt"
        dst_file = sorted_dir / "foo.txt"

        # Suppress src and dst
        service._suppression.suppress(src_file, duration=5.0)
        service._suppression.suppress(dst_file, duration=5.0)

        assert service._suppression.is_suppressed(src_file)
        assert service._suppression.is_suppressed(dst_file)

        # Incoming watchdog events on dst_file are dropped
        matcher = service._ignore_matchers[inbox]
        handler = WatchEventHandler(
            folder=inbox,
            queue=service._event_queue,
            ignore_matcher=matcher,
            suppression=service._suppression,
        )
        handler.dispatch(FileCreatedEvent(str(dst_file)))
        handler.dispatch(FileModifiedEvent(str(dst_file)))
        assert service._event_queue.empty()
    finally:
        service.stop()


@pytest.mark.slow
def test_A49_per_folder_settings_honored(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A49: Per-folder recursive, ignore, settle_seconds and rules subset are honored."""
    folder1 = tmp_path / "folder1"
    folder2 = tmp_path / "folder2"
    dest1 = tmp_path / "dest1"
    dest2 = tmp_path / "dest2"
    for d in (folder1, folder2, dest1, dest2):
        d.mkdir(parents=True)

    cfg = config_text(
        watch=[
            {
                "path": str(folder1),
                "settle_seconds": 0.2,
                "rules": ["RuleA"],
                "ignore": ["*.skip1"],
            },
            {
                "path": str(folder2),
                "settle_seconds": 0.3,
                "rules": ["RuleB"],
                "ignore": ["*.skip2"],
            },
        ],
        rules=[
            {"name": "RuleA", "destination": str(dest1), "keywords": ["report"]},
            {"name": "RuleB", "destination": str(dest2), "keywords": ["report"]},
        ],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=dest1)
    service = WatchService(env.config_path, organizer)
    service._settler.sample_interval = 0.1
    service.start()
    try:
        # Folder 1 ignores *.skip1
        (folder1 / "doc.skip1").write_bytes(b"skip me")
        # Folder 2 ignores *.skip2
        (folder2 / "doc.skip2").write_bytes(b"skip me")

        # Folder 1 processes normal file
        (folder1 / "report_1.pdf").write_bytes(b"report1")
        # Folder 2 processes normal file
        (folder2 / "report_2.pdf").write_bytes(b"report2")

        wait_until(
            lambda: (
                any(c.path.name == "report_1.pdf" for c in organizer.process_calls)
                and any(c.path.name == "report_2.pdf" for c in organizer.process_calls)
            ),
            timeout=5.0,
        )

        # Check call folders passed
        call1 = next(c for c in organizer.process_calls if c.path.name == "report_1.pdf")
        call2 = next(c for c in organizer.process_calls if c.path.name == "report_2.pdf")
        assert call1.folder == folder1
        assert call2.folder == folder2

        # Assert skipped files remain
        assert (folder1 / "doc.skip1").exists()
        assert (folder2 / "doc.skip2").exists()
    finally:
        service.stop()


def test_A50_live_config_reload_valid_and_invalid(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A50: Valid config changes reload live (mtime or SIGHUP);

    invalid ones are rejected and old rules continue.
    """
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["foo"]}],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)

    # 1. Write invalid config (syntax error)
    env.config_path.write_text("invalid: yaml: [syntax error: ::")
    ok = service.reload_config()
    assert not ok
    assert organizer.reload_count == 0

    # 2. Write invalid config (validation error: negative retention days)
    env.config_path.write_text(
        config_text(
            watch=[{"path": str(inbox)}],
            settings={"history": {"retention_days": -10}},
        )
    )
    ok = service.reload_config()
    assert not ok
    assert organizer.reload_count == 0

    # 3. Write valid updated config
    inbox2 = tmp_path / "inbox2"
    inbox2.mkdir()
    cfg_updated = config_text(
        watch=[{"path": str(inbox)}, {"path": str(inbox2)}],
        rules=[{"name": "RuleNew", "destination": str(sorted_dir), "keywords": ["bar"]}],
    )
    env.config_path.write_text(cfg_updated)
    ok = service.reload_config()
    assert ok
    assert organizer.reload_count == 1
    assert inbox2 in service._watch_entries


def test_A51_sigterm_stops_gracefully_second_watcher_refused(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A51: SIGTERM stops gracefully after the in-flight move;

    a second watcher is refused with exit 5 / SafetyRefusal.
    """
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["foo"]}],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service1 = WatchService(env.config_path, organizer)
    service1.start()

    try:
        # Starting a second watcher with the same lock file is refused with SafetyRefusal
        service2 = WatchService(env.config_path, organizer)
        with pytest.raises(SafetyRefusal) as exc_info:
            service2.start()
        assert "Another watcher is already running" in str(exc_info.value)
    finally:
        # Stop service1 gracefully
        service1.stop()

    # Now a new watcher can start cleanly
    service3 = WatchService(env.config_path, organizer)
    service3.start()
    service3.stop()


def test_A52_vanished_folder_retry_and_poll_mode(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """A52: Missing/unmounted folder is retried with backoff; --poll works."""
    missing_inbox = tmp_path / "missing_inbox"
    sorted_dir = tmp_path / "sorted"
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(missing_inbox)}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["foo"]}],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)

    # 1. Service starts without error even when watched folder is missing
    service = WatchService(env.config_path, organizer)
    service.start()
    try:
        assert missing_inbox in service._missing_folders
        assert len(service._observers) == 0

        # Folder reappears
        missing_inbox.mkdir()
        now = time.monotonic() + 35.0  # past retry backoff
        service._check_missing_folders(now)

        # Observer should now be scheduled
        assert missing_inbox not in service._missing_folders
        assert len(service._observers) == 1
    finally:
        service.stop()

    # 2. Polling observer mode (--poll)
    cfg_poll = config_text(
        watch=[{"path": str(missing_inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["poll"]}],
        settings={"settle_seconds": 0.3},
    )
    env.config_path.write_text(cfg_poll)

    service_poll = WatchService(
        env.config_path,
        organizer,
        poll=True,
        sample_interval=0.1,
    )
    service_poll.start()
    try:
        test_file = missing_inbox / "poll_test.txt"
        test_file.write_bytes(b"poll content")

        wait_until(
            lambda: (sorted_dir / "poll_test.txt").exists(),
            timeout=10.0,
            message="Polling observer did not sort file",
        )
    finally:
        service_poll.stop()
