"""Path resolution, directory creation with 0700 permissions, and path expansion."""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

from .models import ConfigError


def _ensure_dir_0700(path: Path) -> Path:
    """Ensure directory exists with 0700 permissions."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        path.chmod(0o700)
    return path


def config_path() -> Path:
    """Path to config.yaml (honors CLUTTERXP_CONFIG, default ~/.config/clutterxp/config.yaml)."""
    if "CLUTTERXP_CONFIG" in os.environ:
        return Path(os.environ["CLUTTERXP_CONFIG"])
    return Path.home() / ".config" / "clutterxp" / "config.yaml"


def state_dir() -> Path:
    """Path to state directory (honors CLUTTERXP_STATE_DIR, default ~/.local/state/clutterxp/).

    Created with 0700 permissions when accessed.
    """
    if "CLUTTERXP_STATE_DIR" in os.environ:
        return _ensure_dir_0700(Path(os.environ["CLUTTERXP_STATE_DIR"]))
    return _ensure_dir_0700(Path.home() / ".local" / "state" / "clutterxp")


def cache_dir() -> Path:
    """Path to cache directory (honors CLUTTERXP_CACHE_DIR, default ~/.cache/clutterxp/).

    Created with 0700 permissions when accessed.
    """
    if "CLUTTERXP_CACHE_DIR" in os.environ:
        return _ensure_dir_0700(Path(os.environ["CLUTTERXP_CACHE_DIR"]))
    return _ensure_dir_0700(Path.home() / ".cache" / "clutterxp")


def history_db_path() -> Path:
    """Path to history SQLite database (~/.local/state/clutterxp/history.db)."""
    return state_dir() / "history.db"


def log_path() -> Path:
    """Path to log file (~/.local/state/clutterxp/clutterxp.log)."""
    return state_dir() / "clutterxp.log"


def fs_lock_path() -> Path:
    """Path to filesystem operation lock (~/.local/state/clutterxp/fs.lock)."""
    return state_dir() / "fs.lock"


def watch_lock_path() -> Path:
    """Path to watch daemon lock (~/.local/state/clutterxp/watch.lock)."""
    return state_dir() / "watch.lock"


def cache_db_path() -> Path:
    """Path to hash cache database (~/.cache/clutterxp/cache.db)."""
    return cache_dir() / "cache.db"


def expand(path_str: str, *, environ: dict[str, str] | None = None) -> Path:
    """Expand ~ and $VAR / ${VAR} in path_str.

    Raises ConfigError on unresolved or invalid environment variables.
    Does NOT touch template placeholders like {year} or {month}.
    """
    env = os.environ if environ is None else environ

    # Tilde expansion
    if environ is not None and "HOME" in environ:
        if path_str == "~":
            path_str = environ["HOME"]
        elif path_str.startswith("~/"):
            path_str = environ["HOME"] + path_str[1:]
        elif path_str.startswith("~"):
            path_str = str(Path(path_str).expanduser())
    else:
        path_str = str(Path(path_str).expanduser())

    # Check for unclosed variable braces
    if re.search(r"\$\{[^}]*$", path_str):
        raise ConfigError(f"Unclosed variable reference in path: '{path_str}'")

    if re.search(r"\$\{\s*\}", path_str):
        raise ConfigError(f"Empty variable reference in path: '{path_str}'")

    var_pattern = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name not in env:
            raise ConfigError(f"Unresolved environment variable: ${name}")
        return env[name]

    result = var_pattern.sub(_sub, path_str)

    if "${" in result:
        raise ConfigError(f"Unresolved or invalid variable reference in: '{path_str}'")

    return Path(result)
