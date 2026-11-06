"""Configuration: schema (frozen field names/types), loader, writer, validation."""

from __future__ import annotations

from .jsonschema import config_json_schema
from .loader import load
from .placeholders import ALLOWED_PLACEHOLDERS, extract_static_root, validate_destination_template
from .runtime import RuntimeConfig
from .schema import Conditions, Config, HistorySettings, RuleSpec, Settings, WatchEntry
from .validate import validate
from .writer import (
    add_rule,
    disable_rule,
    edit_rule,
    enable_rule,
    init_config,
    remove_rule,
)

__all__ = [
    "ALLOWED_PLACEHOLDERS",
    "Conditions",
    "Config",
    "HistorySettings",
    "RuleSpec",
    "RuntimeConfig",
    "Settings",
    "WatchEntry",
    "add_rule",
    "config_json_schema",
    "disable_rule",
    "edit_rule",
    "enable_rule",
    "extract_static_root",
    "init_config",
    "load",
    "remove_rule",
    "validate",
    "validate_destination_template",
]
