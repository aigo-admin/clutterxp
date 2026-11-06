"""Configuration loader with Pydantic validation, error aggregation, and path expansion."""

from __future__ import annotations

import contextlib
import difflib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ..models import ConfigError
from ..paths import expand
from .placeholders import validate_destination_template
from .runtime import RuntimeConfig
from .schema import Conditions, Config, HistorySettings, RuleSpec, Settings, WatchEntry
from .validate import validate

MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]
_MIGRATION_REGISTRY: dict[int, MigrationFn] = {}


def register_migration(from_version: int, fn: MigrationFn) -> None:
    """Register a migration function from version N to N+1."""
    _MIGRATION_REGISTRY[from_version] = fn


def apply_migrations(data: dict[str, Any], target_version: int = 1) -> dict[str, Any]:
    """Apply sequential migrations up to target_version."""
    current_v = data.get("version", 1)
    if not isinstance(current_v, int):
        return data
    while current_v < target_version:
        if current_v not in _MIGRATION_REGISTRY:
            raise ConfigError(
                f"No migration registered from config version {current_v} to {current_v + 1}"
            )
        data = _MIGRATION_REGISTRY[current_v](data)
        current_v += 1
        data["version"] = current_v
    return data


def _format_loc(loc: tuple[int | str, ...]) -> str:
    """Format Pydantic error loc into a YAML-style path (e.g. rules[0].destination)."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            if parts:
                parts.append(f".{item}")
            else:
                parts.append(str(item))
    return "".join(parts)


def _get_allowed_keys_for_loc(parent_loc: tuple[int | str, ...]) -> list[str]:
    """Return valid field names for the model at the given loc prefix."""
    if not parent_loc:
        return list(Config.model_fields.keys())
    if parent_loc[0] == "settings":
        if len(parent_loc) > 1 and parent_loc[1] == "history":
            return list(HistorySettings.model_fields.keys())
        return list(Settings.model_fields.keys())
    if parent_loc[0] == "rules":
        if len(parent_loc) > 2 and parent_loc[2] == "conditions":
            return list(Conditions.model_fields.keys())
        return list(RuleSpec.model_fields.keys())
    if parent_loc[0] == "watch":
        return list(WatchEntry.model_fields.keys())
    return []


def load(path: Path) -> RuntimeConfig:
    """Load, parse, validate, and expand configuration from path.

    Returns a RuntimeConfig with accumulated errors and warnings.
    Raises ConfigError only for fatal parse or file access errors.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    if not path.is_file():
        raise ConfigError(f"Config path is not a file: {path}")

    yaml = YAML(typ="safe")

    try:
        with path.open("r", encoding="utf-8") as f:
            raw_data = yaml.load(f)
    except YAMLError as e:
        raise ConfigError(f"YAML parse error in '{path}': {e}") from e
    except OSError as e:
        raise ConfigError(f"Failed to read config file '{path}': {e}") from e

    if raw_data is None:
        raw_data = {}

    if not isinstance(raw_data, dict):
        raise ConfigError(
            f"Config root in '{path}' must be a YAML mapping, got {type(raw_data).__name__}"
        )

    errors: list[str] = []
    warnings: list[str] = []

    # Check version
    raw_version = raw_data.get("version")
    if raw_version is not None and isinstance(raw_version, int) and raw_version > 1:
        errors.append(
            f"version: config version {raw_version} is newer than supported (1); upgrade ClutterXP"
        )

    # Schema validation with Pydantic (collects all schema errors)
    config_obj: Config | None = None
    try:
        config_obj = Config.model_validate(raw_data)
    except ValidationError as ve:
        for err in ve.errors():
            loc = err["loc"]
            yaml_path = _format_loc(loc)
            msg = err["msg"]
            if err.get("type") == "extra_forbidden":
                unknown = str(loc[-1]) if loc else ""
                allowed = _get_allowed_keys_for_loc(loc[:-1])
                matches = difflib.get_close_matches(unknown, allowed)
                if matches:
                    errors.append(
                        f"{yaml_path}: unknown key '{unknown}'; did you mean '{matches[0]}'?"
                    )
                else:
                    errors.append(f"{yaml_path}: unknown key '{unknown}'")
            else:
                errors.append(f"{yaml_path}: {msg}")

    # Check template placeholders across raw rules to report all problems at once
    raw_rules = raw_data.get("rules", [])
    if isinstance(raw_rules, list):
        for i, r in enumerate(raw_rules):
            if isinstance(r, dict) and isinstance(r.get("destination"), str):
                dest_str = r["destination"]
                if "$" in dest_str or dest_str.startswith("~"):
                    with contextlib.suppress(ConfigError):
                        dest_str = str(expand(dest_str))
                p_errors = validate_destination_template(dest_str)
                for pe in p_errors:
                    err_msg = f"rules[{i}].destination: {pe}"
                    if err_msg not in errors:
                        errors.append(err_msg)

    # Further validation if schema parsed successfully
    if config_obj is not None:
        # 1. Expand paths in destinations and watch entries
        expanded_rules: list[RuleSpec] = []
        for i, rule in enumerate(config_obj.rules):
            if "$" in rule.destination or rule.destination.startswith("~"):
                try:
                    expanded_dest = str(expand(rule.destination))
                    rule = rule.model_copy(update={"destination": expanded_dest})
                except ConfigError as ce:
                    err_msg = f"rules[{i}].destination: {ce.message}"
                    if err_msg not in errors:
                        errors.append(err_msg)
            expanded_rules.append(rule)

        expanded_watch: list[WatchEntry] = []
        for i, entry in enumerate(config_obj.watch):
            try:
                expanded_p = str(expand(entry.path))
                entry = entry.model_copy(update={"path": expanded_p})
            except ConfigError as ce:
                err_msg = f"watch[{i}].path: {ce.message}"
                if err_msg not in errors:
                    errors.append(err_msg)
            expanded_watch.append(entry)

        # 2. Cross-config checks
        updated_config = config_obj.model_copy(
            update={"rules": expanded_rules, "watch": expanded_watch}
        )
        cross_errors, cross_warnings = validate(updated_config)
        errors.extend(cross_errors)
        warnings.extend(cross_warnings)

        enabled_rules = tuple(r for r in expanded_rules if r.enabled)
        disabled_rules = tuple(r for r in expanded_rules if not r.enabled)
        watch_entries = tuple(expanded_watch)
        settings = config_obj.settings
    else:
        enabled_rules = ()
        disabled_rules = ()
        watch_entries = ()
        settings = Settings()

    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0

    return RuntimeConfig(
        config_path=path,
        mtime_ns=mtime_ns,
        settings=settings,
        watch_entries=watch_entries,
        rules=enabled_rules,
        disabled_rules=disabled_rules,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
