"""Tests for safety module, denylist enforcement, root refusal, and permissions."""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

import pytest

# Ensure worktree's src is prioritized
_worktree_src = Path(__file__).resolve().parents[2] / "src"
if str(_worktree_src) not in sys.path:
    sys.path.insert(0, str(_worktree_src))

import clutterxp  # noqa: E402

_cxp_pkg = str(_worktree_src / "clutterxp")
if _cxp_pkg not in clutterxp.__path__:
    clutterxp.__path__.insert(0, _cxp_pkg)

import clutterxp.config  # noqa: E402

_cxp_cfg_pkg = str(_worktree_src / "clutterxp" / "config")
if _cxp_cfg_pkg not in clutterxp.config.__path__:
    clutterxp.config.__path__.insert(0, _cxp_cfg_pkg)

from clutterxp.config.writer import add_rule, init_config  # noqa: E402
from clutterxp.logging_setup import setup_logging  # noqa: E402
from clutterxp.models import SafetyRefusal  # noqa: E402
from clutterxp.paths import cache_dir, state_dir  # noqa: E402
from clutterxp.safety import (  # noqa: E402
    DENYLIST,
    check_destination,
    check_source_root,
    is_denylisted,
    refuse_root,
)
from tests.conftest import EnvPaths  # noqa: E402


def test_A25_denylist_and_root_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A25: denylisted system directories and root execution are refused."""
    # 1. Denylisted system directories
    for p in ("/etc", "/bin", "/usr/bin", "/boot", "/var/log"):
        with pytest.raises(SafetyRefusal):
            check_source_root(Path(p), recursive=False)
        with pytest.raises(SafetyRefusal):
            check_destination(Path(p) / "sub")

    # 2. Root execution refusal
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(SafetyRefusal, match="running as root"):
        refuse_root(allow_root=False)

    # Allowed when allow_root=True
    refuse_root(allow_root=True)


def test_S10_root_refusal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """S10: Root refusal - geteuid == 0 raises SafetyRefusal unless allow_root=True."""
    safe_path = tmp_path / "safe"
    safe_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(os, "geteuid", lambda: 0)

    # refuse_root raises
    with pytest.raises(SafetyRefusal):
        refuse_root(allow_root=False)
    # allow_root waives
    refuse_root(allow_root=True)

    # check_source_root checks root
    with pytest.raises(SafetyRefusal):
        check_source_root(safe_path, recursive=False, allow_root=False)
    check_source_root(safe_path, recursive=False, allow_root=True)

    # check_destination checks root
    with pytest.raises(SafetyRefusal):
        check_destination(safe_path / "dest", allow_root=False)
    check_destination(safe_path / "dest", allow_root=True)


def test_S11_denylist_and_symlinks_refused(tmp_path: Path) -> None:
    """S11: Denylisted directories are never roots or destinations, including symlinks."""
    # Exact denylist items
    for denied in DENYLIST:
        assert is_denylisted(denied)
        with pytest.raises(SafetyRefusal):
            check_source_root(denied, recursive=False)
        with pytest.raises(SafetyRefusal):
            check_destination(denied)

    # Subdirectories of denylist
    sub_paths = [
        Path("/etc/shadow"),
        Path("/usr/local/bin"),
        Path("/var/log/nginx/access.log"),
        Path("/var/lib/dpkg"),
    ]
    for sp in sub_paths:
        assert is_denylisted(sp)
        with pytest.raises(SafetyRefusal):
            check_source_root(sp, recursive=False)
        with pytest.raises(SafetyRefusal):
            check_destination(sp)

    # /root behavior
    assert is_denylisted(Path("/root"), allow_root=False)
    assert not is_denylisted(Path("/root"), allow_root=True)

    # /var/tmp is allowed
    assert not is_denylisted(Path("/var/tmp"))

    # Symlink trick: symlink pointing to /etc
    sneaky_sym = tmp_path / "sneaky_symlink"
    sneaky_sym.symlink_to("/etc")
    assert is_denylisted(sneaky_sym / "passwd")
    with pytest.raises(SafetyRefusal):
        check_source_root(sneaky_sym / "hosts", recursive=False)
    with pytest.raises(SafetyRefusal):
        check_destination(sneaky_sym / "new_dir")

    # Destination hidden component checks
    hidden_dest = tmp_path / ".hidden_dest"
    with pytest.raises(SafetyRefusal, match="hidden component"):
        check_destination(hidden_dest)

    sub_hidden = tmp_path / "visible" / ".secret"
    with pytest.raises(SafetyRefusal, match="hidden component"):
        check_destination(sub_hidden)

    # Symlink pointing to hidden directory
    real_hidden = tmp_path / ".secret_store"
    real_hidden.mkdir()
    visible_link = tmp_path / "visible_link"
    visible_link.symlink_to(real_hidden)
    with pytest.raises(SafetyRefusal, match="hidden component"):
        check_destination(visible_link / "target")

    # Home directory checks: recursive $HOME refused, non-recursive allowed
    home = Path.home()
    with pytest.raises(SafetyRefusal, match="Recursive watch or scan of \\$HOME"):
        check_source_root(home, recursive=True)
    check_source_root(home, recursive=False)


def test_S12_state_files_permissions_0600_dirs_0700(env: EnvPaths, tmp_path: Path) -> None:
    """S12: All state files are created with 0600 (dirs 0700)."""
    # 1. State and cache directories
    s_dir = state_dir()
    c_dir = cache_dir()
    s_mode = stat.S_IMODE(s_dir.stat().st_mode)
    c_mode = stat.S_IMODE(c_dir.stat().st_mode)
    assert s_mode == 0o700, f"state_dir mode: {oct(s_mode)}"
    assert c_mode == 0o700, f"cache_dir mode: {oct(c_mode)}"

    # 2. Log file created with 0600
    custom_log = env.state_dir / "custom_test.log"
    setup_logging(verbosity=1, log_file=custom_log)
    logger = logging.getLogger("clutterxp")
    logger.info("Test log message for permissions check")
    for h in logger.handlers:
        h.flush()

    assert custom_log.exists()
    log_mode = stat.S_IMODE(custom_log.stat().st_mode)
    assert log_mode == 0o600, f"log file mode: {oct(log_mode)}"

    # 3. Config files created with 0600 and parent 0700
    cfg = init_config(env.config_path, minimal=True)
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.parent.stat().st_mode) == 0o700

    # 4. Writer creates .bak and writes with 0600
    add_rule(cfg, {"name": "TestR", "destination": "~/Dest", "keywords": ["t"]})
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    bak = cfg.with_name(cfg.name + ".bak")
    assert stat.S_IMODE(bak.stat().st_mode) == 0o600
