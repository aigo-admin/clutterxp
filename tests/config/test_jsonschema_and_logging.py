"""Tests for jsonschema and logging_setup."""

from __future__ import annotations

import logging
from pathlib import Path

from clutterxp.config.jsonschema import config_json_schema
from clutterxp.logging_setup import setup_logging


def test_config_json_schema() -> None:
    schema = config_json_schema()
    assert isinstance(schema, dict)
    assert schema.get("title") == "Config"
    props = schema.get("properties", {})
    assert "version" in props
    assert "settings" in props
    assert "watch" in props
    assert "rules" in props


def test_logging_setup_verbosity_levels(tmp_path: Path) -> None:
    log_f = tmp_path / "test.log"

    # Verbosity 0 (WARNING)
    setup_logging(verbosity=0, log_file=log_f)
    logger = logging.getLogger("clutterxp")
    handlers0 = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")
    ]
    assert handlers0[0].level == logging.WARNING

    # Verbosity 1 (INFO)
    setup_logging(verbosity=1, log_file=log_f)
    handlers1 = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")
    ]
    assert handlers1[0].level == logging.INFO

    # Verbosity 2 (DEBUG)
    setup_logging(verbosity=2, log_file=log_f)
    handlers2 = [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not hasattr(h, "baseFilename")
    ]
    assert handlers2[0].level == logging.DEBUG
