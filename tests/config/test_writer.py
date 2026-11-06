"""Tests for configuration writer (writer.py)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from clutterxp.config.schema import RuleSpec
from clutterxp.config.writer import (
    add_rule,
    disable_rule,
    edit_rule,
    enable_rule,
    init_config,
    remove_rule,
)
from clutterxp.models import ConfigError

GOLDEN_EXAMPLE = """# yaml-language-server: $schema=./clutterxp.schema.json
# Important header comment that must be preserved.

version: 1

settings:
  collision: skip  # inline comment
  allow_chain: false

watch:
  - path: ~/Downloads

rules:
  # Rule for Project Alpha
  - name: ProjectAlpha
    priority: 10
    keywords: [alpha]
    destination: ~/Projects/ProjectAlpha
"""


def test_A63_init_permissions_and_no_overwrite(tmp_path: Path) -> None:
    """A63: init writes config with 0600 permissions and refuses overwrite without --force."""
    target_config = tmp_path / "subdir" / "config.yaml"

    # 1. Initialize config
    res_path = init_config(target_config, minimal=False)
    assert res_path == target_config
    assert target_config.exists()

    # Check file permissions are 0600
    mode = stat.S_IMODE(target_config.stat().st_mode)
    assert mode == 0o600, f"Expected 0600 mode, got {oct(mode)}"

    # Check parent directory permissions are 0700
    parent_mode = stat.S_IMODE(target_config.parent.stat().st_mode)
    assert parent_mode == 0o700, f"Expected 0700 mode, got {oct(parent_mode)}"

    # 2. Refuses overwrite without force
    with pytest.raises(FileExistsError, match="already exists"):
        init_config(target_config, force=False)

    # 3. Allows overwrite with force=True
    init_config(target_config, force=True, minimal=True)
    assert "watch:" in target_config.read_text(encoding="utf-8")


def test_A64_rules_edit_preserves_comments_and_bak(tmp_path: Path) -> None:
    """A64: rules add/edit/remove/enable/disable preserve comments, validate, and keep .bak."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(GOLDEN_EXAMPLE, encoding="utf-8")
    config_file.chmod(0o600)

    # 1. Add rule using RuleSpec object
    new_rule_spec = RuleSpec(
        name="BetaRule",
        priority=25,
        keywords=["beta"],
        destination="~/Projects/Beta",
    )
    add_rule(config_file, new_rule_spec)

    content_after_add = config_file.read_text(encoding="utf-8")
    # Verify comments and formatting preserved
    assert "# Important header comment that must be preserved." in content_after_add
    assert "# Rule for Project Alpha" in content_after_add
    assert "# inline comment" in content_after_add
    assert "name: BetaRule" in content_after_add

    # Verify .bak was created with previous content
    bak_file = tmp_path / "config.yaml.bak"
    assert bak_file.exists()
    assert bak_file.read_text(encoding="utf-8") == GOLDEN_EXAMPLE
    assert stat.S_IMODE(bak_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600

    # 2. Edit rule (including renaming to a new non-colliding name)
    edit_rule(config_file, "BetaRule", {"name": "RenamedBeta", "priority": 30})
    content_after_edit = config_file.read_text(encoding="utf-8")
    assert "RenamedBeta" in content_after_edit
    assert "priority: 30" in content_after_edit
    assert "# Important header comment" in content_after_edit

    # Renaming to an existing name raises ConfigError
    with pytest.raises(ConfigError, match="already exists"):
        edit_rule(config_file, "RenamedBeta", {"name": "ProjectAlpha"})

    # 3. Disable / Enable rule
    disable_rule(config_file, "RenamedBeta")
    assert "enabled: false" in config_file.read_text(encoding="utf-8")
    enable_rule(config_file, "RenamedBeta")
    assert "enabled: true" in config_file.read_text(encoding="utf-8")

    # 4. Remove rule
    remove_rule(config_file, "RenamedBeta")
    content_after_remove = config_file.read_text(encoding="utf-8")
    assert "RenamedBeta" not in content_after_remove
    assert "ProjectAlpha" in content_after_remove
    assert "# Important header comment" in content_after_remove


def test_invalid_edit_leaves_file_byte_identical(tmp_path: Path) -> None:
    """Validation failure aborts and leaves original file byte-identical and untouched."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(GOLDEN_EXAMPLE, encoding="utf-8")
    config_file.chmod(0o600)
    original_bytes = config_file.read_bytes()

    bak_file = tmp_path / "config.yaml.bak"
    assert not bak_file.exists()

    # Attempt invalid edit (empty destination violates schema)
    with pytest.raises(ConfigError):
        edit_rule(config_file, "ProjectAlpha", {"destination": ""})

    # Assert byte-identical
    assert config_file.read_bytes() == original_bytes
    # No .bak created for aborted operation
    assert not bak_file.exists()


def test_rule_not_found_and_duplicate_names(tmp_path: Path) -> None:
    """Writer reports ConfigError for missing rules and duplicate rule names."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(GOLDEN_EXAMPLE, encoding="utf-8")

    # Empty name error
    with pytest.raises(ConfigError, match="non-empty name"):
        add_rule(config_file, {"name": "", "destination": "~/Dest", "keywords": ["k"]})

    # Non-existent rule
    with pytest.raises(ConfigError, match="not found"):
        edit_rule(config_file, "NonExistent", {"priority": 99})

    with pytest.raises(ConfigError, match="not found"):
        remove_rule(config_file, "NonExistent")

    # Duplicate name on add (case-insensitive)
    with pytest.raises(ConfigError, match="already exists"):
        add_rule(
            config_file,
            {"name": "projectalpha", "destination": "~/Dest", "keywords": ["test"]},
        )

    # Missing config file
    with pytest.raises(ConfigError, match="not found"):
        add_rule(tmp_path / "missing.yaml", {"name": "R", "destination": "~/D", "keywords": ["k"]})

    # Non-dict config file
    non_dict = tmp_path / "not_dict.yaml"
    non_dict.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a valid YAML mapping"):
        add_rule(non_dict, {"name": "R", "destination": "~/D", "keywords": ["k"]})
