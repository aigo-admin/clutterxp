"""Unit tests for WatchService features and edge cases."""

from __future__ import annotations

import errno
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clutterxp.models import ClutterError, ErrorCode
from clutterxp.watch.service import WatchService
from tests.conftest import ConfigTextFn, EnvPaths
from tests.watch.conftest import FakeOrganizer


def test_initial_scan_moves_existing_files(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """initial_scan: true organizes files already present at startup."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    # File exists BEFORE service starts
    existing = inbox / "pre_existing.pdf"
    existing.write_bytes(b"existing content")

    cfg = config_text(
        watch=[{"path": str(inbox), "initial_scan": True}],
        rules=[{"name": "RuleInit", "destination": str(sorted_dir), "keywords": ["pre"]}],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service.start()
    try:
        # File was sorted during start's initial scan
        assert (sorted_dir / "pre_existing.pdf").exists()
        assert not existing.exists()
    finally:
        service.stop()


def test_initial_scan_dry_run(env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path) -> None:
    """initial_scan: true with dry_run: true plans but moves zero files."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    existing = inbox / "dry_test.pdf"
    existing.write_bytes(b"content")

    cfg = config_text(
        watch=[{"path": str(inbox), "initial_scan": True}],
        rules=[{"name": "RuleInit", "destination": str(sorted_dir), "keywords": ["dry"]}],
    )
    env.config_path.write_text(cfg)

    events: list[dict[str, str]] = []
    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(
        env.config_path,
        organizer,
        dry_run=True,
        on_event=lambda e: events.append(e),
    )
    service.start()
    try:
        # Nothing moved
        assert existing.exists()
        assert not (sorted_dir / "dry_test.pdf").exists()
        assert any(e.get("event") == "dry_run" for e in events)
    finally:
        service.stop()


def test_inotify_enospc_error(env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path) -> None:
    """inotify limit reached (ENOSPC on watch add) raises ClutterError with fix command."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["test"]}],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)

    # Mock observer.schedule to simulate ENOSPC
    mock_obs = MagicMock()
    mock_obs.schedule.side_effect = OSError(errno.ENOSPC, "No space left on device")
    service._create_observer = MagicMock(return_value=mock_obs)  # type: ignore[method-assign]

    with pytest.raises(ClutterError) as exc_info:
        service._setup_watchers()

    assert "fs.inotify.max_user_watches" in str(exc_info.value)
    assert exc_info.value.code == ErrorCode.PERMISSION


def test_reconcile_folder(env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path) -> None:
    """Reconciliation scan processes folder files."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["rec"]}],
    )
    env.config_path.write_text(cfg)

    file_to_scan = inbox / "rec_file.txt"
    file_to_scan.write_bytes(b"data")

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)
    service._setup_watchers()

    service._reconcile_folder(inbox)
    assert (sorted_dir / "rec_file.txt").exists()
    assert not file_to_scan.exists()


def test_on_event_callback_error_resilience(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """Exceptions in user on_event callback are logged and do not crash the service."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["test"]}],
    )
    env.config_path.write_text(cfg)

    def failing_cb(_event: dict[str, str]) -> None:
        raise RuntimeError("User callback exploded")

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer, on_event=failing_cb)

    # Calling _emit_event directly should catch and log RuntimeError
    service._emit_event(
        event_type="moved",
        src=inbox / "file.txt",
        dst=sorted_dir / "file.txt",
        rule="TestRule",
    )


def test_candidate_process_error_handling(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """Organizer.process_file exception is caught and emitted as error event."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.01}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["test"]}],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    organizer.process_file = MagicMock(side_effect=OSError("Disk I/O error"))  # type: ignore[method-assign]

    events: list[dict[str, str]] = []
    service = WatchService(
        env.config_path,
        organizer,
        sample_interval=0.01,
        on_event=lambda e: events.append(e),
    )
    service._setup_watchers()

    f = inbox / "test_file.txt"
    f.write_bytes(b"content")

    # Fast settle: sample 1, then sample 2
    service._settler.check(f, 0.0)
    time.sleep(0.02)
    service._process_candidate(f)

    assert any(e.get("event") == "error" for e in events)


def test_dry_run_process_candidate(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """_process_candidate under dry_run: true does not move files."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.01}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["test"]}],
    )
    env.config_path.write_text(cfg)

    events: list[dict[str, str]] = []
    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(
        env.config_path,
        organizer,
        dry_run=True,
        sample_interval=0.01,
        on_event=lambda e: events.append(e),
    )
    service._setup_watchers()

    f = inbox / "test_candidate.txt"
    f.write_bytes(b"content")

    # Fast settle: sample 1, then sample 2
    service._settler.check(f, 0.0)
    time.sleep(0.02)
    service._process_candidate(f)

    assert f.exists()
    assert not (sorted_dir / "test_candidate.txt").exists()
    assert any(e.get("event") == "dry_run" for e in events)


def test_service_run_synchronous(env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path) -> None:
    """WatchService.run() runs synchronously until stopped."""
    import threading

    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["test"]}],
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(default_dest=sorted_dir)
    service = WatchService(env.config_path, organizer)

    def stopper() -> None:
        time.sleep(0.1)
        service.request_reload()
        service.stop()

    t = threading.Thread(target=stopper)
    t.start()
    service.run()
    t.join()


def test_process_candidate_ambiguous_and_blocked(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """_process_candidate handles blocked and ambiguous files."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.01}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["test"]}],
    )
    env.config_path.write_text(cfg)

    events: list[dict[str, str]] = []
    organizer = FakeOrganizer(
        default_dest=sorted_dir,
        blocked_names=("blocked.txt",),
        ambiguous_names=("ambiguous.txt",),
    )
    service = WatchService(
        env.config_path,
        organizer,
        sample_interval=0.01,
        on_event=lambda e: events.append(e),
    )
    service._setup_watchers()

    # 1. Blocked file
    blocked_file = inbox / "blocked.txt"
    blocked_file.write_bytes(b"blocked")
    service._settler.check(blocked_file, 0.0)
    time.sleep(0.02)
    service._process_candidate(blocked_file)

    assert any(e.get("event") == "blocked" for e in events)

    # 2. Ambiguous file
    ambig_file = inbox / "ambiguous.txt"
    ambig_file.write_bytes(b"ambig")
    service._settler.check(ambig_file, 0.0)
    time.sleep(0.02)
    service._process_candidate(ambig_file)

    assert any(e.get("event") == "ambiguous" for e in events)
