"""Runtime configuration representation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .schema import RuleSpec, Settings, WatchEntry


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    config_path: Path
    mtime_ns: int  # config file mtime for reload detection
    settings: Settings  # from schema.py
    watch_entries: tuple[WatchEntry, ...]  # from schema.py
    rules: tuple[RuleSpec, ...]  # enabled rules only
    disabled_rules: tuple[RuleSpec, ...]  # disabled rules
    errors: tuple[str, ...]  # config errors (empty = valid)
    warnings: tuple[str, ...]  # config warnings

    @property
    def is_valid(self) -> bool:
        """True if configuration has no errors."""
        return len(self.errors) == 0

    @property
    def all_rules(self) -> tuple[RuleSpec, ...]:
        """All rules including disabled ones."""
        return self.rules + self.disabled_rules
