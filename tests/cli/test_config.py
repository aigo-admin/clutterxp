"""Tests for `clutterxp config` subcommands."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from clutterxp.cli.app import app
from tests.conftest import EnvPaths


def test_config_path(env: EnvPaths, runner: CliRunner) -> None:
    """Test `config path` prints resolved path."""
    res = runner.invoke(app, ["config", "path"])
    assert res.exit_code == 0
    assert str(env.config_path) in res.stdout

    res_json = runner.invoke(app, ["--json", "config", "path"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert data["schema_version"] == 1
    assert data["command"] == "config_path"
    assert data["path"] == str(env.config_path)


def test_config_show(env: EnvPaths, runner: CliRunner) -> None:
    """Test `config show` prints effective configuration."""
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["config", "show"])
    assert res.exit_code == 0
    assert "Settings:" in res.stdout
    assert "Watch Folders" in res.stdout

    res_json = runner.invoke(app, ["--json", "config", "show"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert data["schema_version"] == 1
    assert data["command"] == "config_show"
    assert "settings" in data
    assert "rules" in data


def test_config_validate_valid_and_errors(env: EnvPaths, runner: CliRunner) -> None:
    """Test `config validate` reports valid config, errors, and warnings."""
    # 1. Valid config
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["config", "validate"])
    assert res.exit_code == 0
    assert "Configuration is valid" in res.stdout

    # 2. Config with error (unknown placeholder)
    env.config_path.write_text(
        "version: 1\n"
        "rules:\n"
        "  - name: Test\n"
        "    keywords: [test]\n"
        "    destination: ~/Documents/{invalid_placeholder}\n",
        encoding="utf-8",
    )
    res_err = runner.invoke(app, ["config", "validate"])
    assert res_err.exit_code == 2
    assert "error:" in res_err.stdout

    # 3. Config with error in --json mode
    res_json = runner.invoke(app, ["--json", "config", "validate"])
    assert res_json.exit_code == 2
    data = json.loads(res_json.stdout)
    assert data["schema_version"] == 1
    assert not data["valid"]
    assert len(data["errors"]) > 0


def test_config_validate_strict_warnings(env: EnvPaths, runner: CliRunner) -> None:
    """Test `config validate --strict` exits 3 on warning."""
    # Non-existent watch folder triggers warning
    env.config_path.write_text(
        "version: 1\n"
        "watch:\n"
        "  - path: /tmp/nonexistent_watch_folder_12345\n"
        "rules:\n"
        "  - name: Valid\n"
        "    keywords: [valid]\n"
        "    destination: /tmp/dest_folder\n",
        encoding="utf-8",
    )
    # Normal validate succeeds (warning only)
    res = runner.invoke(app, ["config", "validate"])
    assert res.exit_code == 0
    assert "warning:" in res.stdout

    # Strict validate fails with exit code 3
    res_strict = runner.invoke(app, ["config", "validate", "--strict"])
    assert res_strict.exit_code == 3


def test_config_schema(runner: CliRunner) -> None:
    """Test `config schema` outputs valid JSON Schema."""
    res = runner.invoke(app, ["config", "schema"])
    assert res.exit_code == 0
    schema = json.loads(res.stdout)
    assert "$schema" in schema or "properties" in schema
    assert "properties" in schema
    assert "rules" in schema["properties"]


def test_config_edit_success(
    env: EnvPaths,
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test `config edit` opens editor and atomically applies valid edits."""
    runner.invoke(app, ["init", "--minimal"])

    # Create dummy editor script that modifies the config validly
    editor_script = env.state_dir / "fake_editor.sh"
    editor_script.write_text(
        "#!/bin/sh\n"
        "cat << 'EOF' >> \"$1\"\n"
        "rules:\n"
        "  - name: AddedByEditor\n"
        "    keywords: [edit]\n"
        "    destination: ~/Sorted\n"
        "EOF\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor_script))

    res = runner.invoke(app, ["config", "edit"])
    assert res.exit_code == 0
    assert "Configuration successfully updated" in res.stdout

    content = env.config_path.read_text(encoding="utf-8")
    assert "AddedByEditor" in content


def test_config_edit_invalid_discards(
    env: EnvPaths,
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test `config edit` discards changes if validation fails."""
    runner.invoke(app, ["init", "--minimal"])
    original_content = env.config_path.read_text(encoding="utf-8")

    # Create dummy editor script that corrupts the config
    editor_script = env.state_dir / "corrupt_editor.sh"
    editor_script.write_text(
        "#!/bin/sh\necho 'invalid: [yaml: broken' >> \"$1\"\n",
        encoding="utf-8",
    )
    editor_script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor_script))

    res = runner.invoke(app, ["config", "edit"])
    assert res.exit_code == 2
    assert "Changes discarded" in res.stderr or "Changes discarded" in res.stdout

    # Original content remains unchanged
    assert env.config_path.read_text(encoding="utf-8") == original_content


def test_config_migrate(runner: CliRunner) -> None:
    """Test `config migrate` reports current version."""
    res = runner.invoke(app, ["config", "migrate"])
    assert res.exit_code == 0
    assert "up to date" in res.stdout

    res_json = runner.invoke(app, ["--json", "config", "migrate"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert data["schema_version"] == 1
    assert data["status"] == "up_to_date"
    assert data["version"] == 1
