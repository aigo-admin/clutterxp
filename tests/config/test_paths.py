"""Tests for paths module (paths.py)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from clutterxp.models import ConfigError
from clutterxp.paths import (
    cache_db_path,
    cache_dir,
    config_path,
    expand,
    fs_lock_path,
    history_db_path,
    log_path,
    state_dir,
    watch_lock_path,
)


def test_paths_default_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.delenv("CLUTTERXP_CONFIG", raising=False)
    monkeypatch.delenv("CLUTTERXP_STATE_DIR", raising=False)
    monkeypatch.delenv("CLUTTERXP_CACHE_DIR", raising=False)

    cfg = config_path()
    assert cfg == fake_home / ".config" / "clutterxp" / "config.yaml"

    s_dir = state_dir()
    assert s_dir == fake_home / ".local" / "state" / "clutterxp"
    assert s_dir.exists()
    assert stat.S_IMODE(s_dir.stat().st_mode) == 0o700

    c_dir = cache_dir()
    assert c_dir == fake_home / ".cache" / "clutterxp"
    assert c_dir.exists()
    assert stat.S_IMODE(c_dir.stat().st_mode) == 0o700

    assert history_db_path() == s_dir / "history.db"
    assert log_path() == s_dir / "clutterxp.log"
    assert fs_lock_path() == s_dir / "fs.lock"
    assert watch_lock_path() == s_dir / "watch.lock"
    assert cache_db_path() == c_dir / "cache.db"


def test_paths_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom_cfg = tmp_path / "custom" / "c.yaml"
    custom_state = tmp_path / "custom_state"
    custom_cache = tmp_path / "custom_cache"

    monkeypatch.setenv("CLUTTERXP_CONFIG", str(custom_cfg))
    monkeypatch.setenv("CLUTTERXP_STATE_DIR", str(custom_state))
    monkeypatch.setenv("CLUTTERXP_CACHE_DIR", str(custom_cache))

    assert config_path() == custom_cfg
    assert state_dir() == custom_state
    assert custom_state.exists()
    assert stat.S_IMODE(custom_state.stat().st_mode) == 0o700

    assert cache_dir() == custom_cache
    assert custom_cache.exists()
    assert stat.S_IMODE(custom_cache.stat().st_mode) == 0o700

    assert history_db_path() == custom_state / "history.db"
    assert log_path() == custom_state / "clutterxp.log"
    assert fs_lock_path() == custom_state / "fs.lock"
    assert watch_lock_path() == custom_state / "watch.lock"
    assert cache_db_path() == custom_cache / "cache.db"


def test_expand_tilde_and_vars() -> None:
    env = {"BASE": "/opt/store", "SUB": "invoices", "HOME": "/my/home"}

    assert expand("~", environ=env) == Path("/my/home")
    assert expand("~/Downloads", environ=env) == Path("/my/home/Downloads")
    assert expand("$BASE/$SUB", environ=env) == Path("/opt/store/invoices")
    assert expand("${BASE}/${SUB}/files", environ=env) == Path("/opt/store/invoices/files")
    assert expand("~/Data/{year}", environ=env) == Path("/my/home/Data/{year}")


def test_expand_errors() -> None:
    env = {"FOO": "bar"}

    # Unclosed brace
    with pytest.raises(ConfigError, match="Unclosed variable"):
        expand("~/path/${UNCLOSED", environ=env)

    # Empty brace
    with pytest.raises(ConfigError, match="Empty variable"):
        expand("~/path/${}/sub", environ=env)

    # Unresolved variable
    with pytest.raises(ConfigError, match="Unresolved environment variable"):
        expand("~/path/$UNKNOWN_VAR", environ=env)

    with pytest.raises(ConfigError, match="Unresolved environment variable"):
        expand("~/path/${UNKNOWN_VAR}", environ=env)

    # Malformed reference
    with pytest.raises(ConfigError, match="Unresolved or invalid variable reference"):
        expand("~/path/${INVALID:VAR}", environ=env)
