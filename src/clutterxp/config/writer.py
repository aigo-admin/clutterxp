"""Configuration file writer with round-trip comment preservation and atomic writes."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ..models import ConfigError
from ..paths import _ensure_dir_0700, config_path
from .loader import _format_loc
from .schema import Config, RuleSpec
from .validate import validate

DEFAULT_CONFIG_EXAMPLE = """# yaml-language-server: $schema=./clutterxp.schema.json
# ClutterXP example configuration. Copy to ~/.config/clutterxp/config.yaml
# (or run `clutterxp init`). Every name, keyword and folder below is an EXAMPLE:
# nothing is built in. Edit, delete and add your own.

version: 1

settings:
  collision: skip              # skip | rename   (what to do if the destination file name exists)
  create_destinations: leaf    # never | leaf | all
  match: token                 # default keyword mode: token | prefix | substring
  case_sensitive: false
  skip_hidden: true
  follow_symlinks: false
  confirm_threshold: 10        # ask before moving more than this many files at once
  settle_seconds: 3            # how long a file must be quiet before the watcher moves it
  verify_copy: size            # size | hash (only matters for cross-filesystem moves)
  record_hash: false
  history:
    retention_days: 365
    log_no_match: false
  ignore: []                   # extra patterns to ignore everywhere
  allow_chain: false           # keep false: destinations must not live inside watched folders

watch:
  - path: ~/Downloads
    recursive: false
    settle_seconds: 3
    rules: all                 # or a list of rule names that apply to this folder
    initial_scan: false

rules:
  - name: Invoices
    priority: 20
    keywords: [invoice, inv, bill, receipt]
    exclude: [draft]
    extensions: [pdf, png, jpg]
    destination: ~/Documents/Invoices/{year}/{month}
"""


def _load_yaml(path: Path) -> tuple[YAML, Any]:
    """Load YAML in round-trip mode preserving comments."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        with path.open("r", encoding="utf-8") as f:
            doc = yaml.load(f)
    except (YAMLError, OSError) as e:
        raise ConfigError(f"Failed to load configuration '{path}': {e}") from e

    if doc is None or not isinstance(doc, dict):
        raise ConfigError(f"Config file '{path}' is not a valid YAML mapping")

    return yaml, doc


def _validate_doc(doc: Any) -> None:
    """Validate modified in-memory configuration document."""
    try:
        cfg = Config.model_validate(doc)
    except ValidationError as ve:
        err_msgs = tuple(f"{_format_loc(err['loc'])}: {err['msg']}" for err in ve.errors())
        raise ConfigError("Validation failed for modified configuration", err_msgs) from ve

    errors, _ = validate(cfg)
    if errors:
        raise ConfigError("Cross-validation failed for modified configuration", tuple(errors))


def _save_atomic(path: Path, doc: Any, yaml: YAML) -> None:
    """Validate in memory, create .bak backup, and atomically replace file (0600 mode)."""
    _validate_doc(doc)

    # 1. Create .bak backup of the existing file
    bak_path = path.with_name(path.name + ".bak")
    if path.exists():
        shutil.copy2(path, bak_path)
        with contextlib.suppress(OSError):
            bak_path.chmod(0o600)

    # 2. Write to temp file in same directory, fsync, and atomic replace
    _ensure_dir_0700(path.parent)
    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            yaml.dump(doc, f)
            f.flush()
            os.fsync(f.fileno())
        temp_path.chmod(0o600)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()


def add_rule(path: Path, rule: dict[str, Any] | RuleSpec) -> None:
    """Add a new rule to the configuration, validating and preserving comments."""
    yaml, doc = _load_yaml(path)

    rule_dict = rule.model_dump(exclude_unset=True) if isinstance(rule, RuleSpec) else dict(rule)

    rule_name = str(rule_dict.get("name", ""))
    if not rule_name:
        raise ConfigError("Rule must have a non-empty name")

    rules_list = doc.setdefault("rules", [])
    for existing in rules_list:
        if (
            isinstance(existing, dict)
            and existing.get("name", "").casefold() == rule_name.casefold()
        ):
            raise ConfigError(f"Rule with name '{rule_name}' already exists")

    rules_list.append(rule_dict)
    _save_atomic(path, doc, yaml)


def edit_rule(path: Path, rule_name: str, updates: dict[str, Any]) -> None:
    """Edit an existing rule by name, validating and preserving comments."""
    yaml, doc = _load_yaml(path)

    rules_list = doc.get("rules", [])
    target: dict[str, Any] | None = None
    for r in rules_list:
        if isinstance(r, dict) and r.get("name", "").casefold() == rule_name.casefold():
            target = r
            break

    if target is None:
        raise ConfigError(f"Rule '{rule_name}' not found")

    # If updating the name, check for collisions
    if "name" in updates:
        new_name = str(updates["name"])
        for r in rules_list:
            if (
                isinstance(r, dict)
                and r is not target
                and r.get("name", "").casefold() == new_name.casefold()
            ):
                raise ConfigError(f"Rule with name '{new_name}' already exists")

    target.update(updates)
    _save_atomic(path, doc, yaml)


def remove_rule(path: Path, rule_name: str) -> None:
    """Remove a rule by name, validating and preserving comments."""
    yaml, doc = _load_yaml(path)

    rules_list = doc.get("rules", [])
    target_idx = -1
    for i, r in enumerate(rules_list):
        if isinstance(r, dict) and r.get("name", "").casefold() == rule_name.casefold():
            target_idx = i
            break

    if target_idx == -1:
        raise ConfigError(f"Rule '{rule_name}' not found")

    del rules_list[target_idx]
    _save_atomic(path, doc, yaml)


def enable_rule(path: Path, rule_name: str) -> None:
    """Enable a rule by name."""
    edit_rule(path, rule_name, {"enabled": True})


def disable_rule(path: Path, rule_name: str) -> None:
    """Disable a rule by name."""
    edit_rule(path, rule_name, {"enabled": False})


def init_config(path: Path | None = None, *, minimal: bool = False, force: bool = False) -> Path:
    """Initialize a configuration file with 0600 permissions.

    Refuses to overwrite existing config unless force is True.
    """
    target = path if path is not None else config_path()
    if target.exists() and not force:
        raise FileExistsError(
            f"Config file already exists at '{target}'. Pass force=True to overwrite."
        )

    _ensure_dir_0700(target.parent)

    if minimal:
        content = (
            "# yaml-language-server: $schema=./clutterxp.schema.json\n"
            "version: 1\n\n"
            "watch:\n"
            "  - path: ~/Downloads\n"
        )
    else:
        # Search for config.example.yaml in parents
        example_path = Path(__file__).resolve().parents[3] / "config.example.yaml"
        if example_path.exists():
            content = example_path.read_text(encoding="utf-8")
        else:
            content = DEFAULT_CONFIG_EXAMPLE

    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        temp_path.chmod(0o600)
        temp_path.replace(target)
    finally:
        if temp_path.exists():
            with contextlib.suppress(OSError):
                temp_path.unlink()

    return target
