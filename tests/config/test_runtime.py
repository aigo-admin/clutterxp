"""Tests for RuntimeConfig dataclass."""

from __future__ import annotations

from pathlib import Path

import pytest

from clutterxp.config.runtime import RuntimeConfig
from clutterxp.config.schema import RuleSpec, Settings, WatchEntry


def test_runtime_config_immutability() -> None:
    cfg = RuntimeConfig(
        config_path=Path("/tmp/config.yaml"),
        mtime_ns=1000,
        settings=Settings(),
        watch_entries=(WatchEntry(path="~/Downloads"),),
        rules=(RuleSpec(name="R1", destination="~/Dest", keywords=["kw"]),),
        disabled_rules=(),
        errors=(),
        warnings=(),
    )

    assert cfg.is_valid
    assert len(cfg.rules) == 1
    assert len(cfg.disabled_rules) == 0
    assert len(cfg.all_rules) == 1

    with pytest.raises((AttributeError, TypeError)):
        # Cannot mutate frozen dataclass
        cfg.mtime_ns = 2000  # type: ignore[misc]


def test_runtime_config_invalid_state() -> None:
    cfg = RuntimeConfig(
        config_path=Path("/tmp/config.yaml"),
        mtime_ns=1000,
        settings=Settings(),
        watch_entries=(),
        rules=(),
        disabled_rules=(RuleSpec(name="R2", enabled=False, destination="~/Dest", keywords=["kw"]),),
        errors=("rules[0].destination: invalid",),
        warnings=("some warning",),
    )

    assert not cfg.is_valid
    assert len(cfg.errors) == 1
    assert len(cfg.warnings) == 1
    assert len(cfg.rules) == 0
    assert len(cfg.disabled_rules) == 1
    assert len(cfg.all_rules) == 1
