"""Logging configuration with rotating file handler and stderr verbosity."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

from .paths import _ensure_dir_0700, log_path


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that creates files with 0600 permissions."""

    def _open(self) -> Any:
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
        fd = os.open(self.baseFilename, flags, 0o600)
        with contextlib.suppress(OSError):
            Path(self.baseFilename).chmod(0o600)
        return cast(Any, open(fd, mode=self.mode, encoding=self.encoding))  # noqa: PTH123


def setup_logging(verbosity: int = 0, log_file: Path | None = None) -> None:
    """Set up logging for the clutterxp package.

    Args:
        verbosity: 0 = WARNING, 1 = INFO, >= 2 = DEBUG on stderr.
        log_file: Path to rotating log file. Defaults to paths.log_path().
    """
    target_log = log_file if log_file is not None else log_path()
    _ensure_dir_0700(target_log.parent)

    logger = logging.getLogger("clutterxp")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Clear previous handlers to avoid duplicate log records
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    # 1. Stderr handler
    if verbosity >= 2:
        stderr_level = logging.DEBUG
        stderr_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    elif verbosity == 1:
        stderr_level = logging.INFO
        stderr_fmt = "%(levelname)s: %(message)s"
    else:
        stderr_level = logging.WARNING
        stderr_fmt = "%(levelname)s: %(message)s"

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(stderr_level)
    stderr_handler.setFormatter(logging.Formatter(stderr_fmt))
    logger.addHandler(stderr_handler)

    # 2. Rotating file handler (5 x 1 MB)
    file_handler = SafeRotatingFileHandler(
        str(target_log),
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)
