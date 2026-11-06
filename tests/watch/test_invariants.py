"""Safety invariant tests for Watch Service (S2, S9, S14)."""

from __future__ import annotations

from pathlib import Path

import pytest

from clutterxp.config import loader
from clutterxp.models import SafetyRefusal
from clutterxp.watch.service import WatchService
from tests.conftest import ConfigTextFn, EnvPaths
from tests.helpers import wait_until
from tests.watch.conftest import FakeOrganizer


@pytest.mark.slow
def test_S2_no_match_ambiguous_ignored_untouched(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """S2: NO_MATCH, AMBIGUOUS and IGNORED files are never touched."""
    inbox = tmp_path / "inbox"
    sorted_dir = tmp_path / "sorted"
    inbox.mkdir(parents=True)
    sorted_dir.mkdir(parents=True)

    cfg = config_text(
        watch=[{"path": str(inbox), "settle_seconds": 0.3}],
        rules=[{"name": "Rule1", "destination": str(sorted_dir), "keywords": ["valid"]}],
        settings={"settle_seconds": 0.3, "skip_hidden": True},
    )
    env.config_path.write_text(cfg)

    organizer = FakeOrganizer(
        default_dest=sorted_dir,
        unmatched_names=("nomatch.txt",),
        ambiguous_names=("ambiguous.txt",),
    )

    events_emitted: list[dict[str, str]] = []
    service = WatchService(
        env.config_path,
        organizer,
        on_event=lambda e: events_emitted.append(e),
    )
    service._settler.sample_interval = 0.1
    service.start()
    try:
        # Create NO_MATCH file
        nomatch_file = inbox / "nomatch.txt"
        nomatch_file.write_bytes(b"content nomatch")

        # Create AMBIGUOUS file
        ambig_file = inbox / "ambiguous.txt"
        ambig_file.write_bytes(b"content ambiguous")

        # Create IGNORED files (built-in .part and hidden .dotfile)
        ignored_part = inbox / "ignored.part"
        ignored_part.write_bytes(b"part content")
        ignored_dot = inbox / ".hidden"
        ignored_dot.write_bytes(b"hidden content")

        # Create valid match
        valid_file = inbox / "valid_doc.pdf"
        valid_file.write_bytes(b"valid content")

        # Wait until valid file is sorted
        wait_until(
            lambda: (sorted_dir / "valid_doc.pdf").exists() and not valid_file.exists(),
            timeout=5.0,
        )

        # Assert S2: all untouched files remain in inbox intact with identical content
        assert nomatch_file.exists()
        assert nomatch_file.read_bytes() == b"content nomatch"

        assert ambig_file.exists()
        assert ambig_file.read_bytes() == b"content ambiguous"

        assert ignored_part.exists()
        assert ignored_part.read_bytes() == b"part content"

        assert ignored_dot.exists()
        assert ignored_dot.read_bytes() == b"hidden content"

        # None of these were written to destination
        assert not (sorted_dir / "nomatch.txt").exists()
        assert not (sorted_dir / "ambiguous.txt").exists()
        assert not (sorted_dir / "ignored.part").exists()
        assert not (sorted_dir / ".hidden").exists()
    finally:
        service.stop()


def test_S9_destination_inside_watched_folder_rejected(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """S9: A destination inside a watched folder is rejected unless allow_chain: true."""
    inbox = tmp_path / "inbox"
    dest_inside = inbox / "SubDir"
    inbox.mkdir(parents=True)
    dest_inside.mkdir(parents=True)

    # 1. By default allow_chain is False -> validation error
    cfg_loop = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "RuleLoop", "destination": str(dest_inside), "keywords": ["test"]}],
        settings={"allow_chain": False},
    )
    env.config_path.write_text(cfg_loop)

    rt_config = loader.load(env.config_path)
    assert not rt_config.is_valid
    assert any("inside watched folder" in err for err in rt_config.errors)

    # WatchService refuses to start with invalid config
    organizer = FakeOrganizer(default_dest=dest_inside)
    with pytest.raises(SafetyRefusal):
        WatchService(env.config_path, organizer)

    # 2. When allow_chain: True, config is allowed
    cfg_chain = config_text(
        watch=[{"path": str(inbox)}],
        rules=[{"name": "RuleChain", "destination": str(dest_inside), "keywords": ["test"]}],
        settings={"allow_chain": True},
    )
    env.config_path.write_text(cfg_chain)
    rt_chain = loader.load(env.config_path)
    assert rt_chain.is_valid


def test_S14_concurrent_watchers_refused(
    env: EnvPaths, config_text: ConfigTextFn, tmp_path: Path
) -> None:
    """S14: Two processes cannot run watch concurrently (watch lock)."""
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
    service1 = WatchService(env.config_path, organizer)
    service1.start()

    try:
        service2 = WatchService(env.config_path, organizer)
        with pytest.raises(SafetyRefusal):
            service2.start()
    finally:
        service1.stop()
