"""Tests for `clutterxp init` command proving criterion A63."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from clutterxp.cli.app import app
from tests.conftest import EnvPaths


def test_A63_init_writes_commented_config_and_refuses_overwrite(
    env: EnvPaths,
    runner: CliRunner,
) -> None:
    """A63: init writes commented config with 0600 permissions.

    Refuses overwrite without --force.
    """
    assert not env.config_path.exists()

    # 1. Successful initialization
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 0
    assert env.config_path.exists()
    assert "Created configuration at" in res.stdout

    # Invariant S12 / A63: 0600 permissions
    mode = env.config_path.stat().st_mode & 0o777
    assert mode == 0o600

    content = env.config_path.read_text(encoding="utf-8")
    assert "# ClutterXP example configuration" in content
    assert "rules:" in content

    # 2. Refusal to overwrite without --force
    res_overwrite = runner.invoke(app, ["init"])
    assert res_overwrite.exit_code == 2
    assert (
        "Pass --force to overwrite" in res_overwrite.stderr
        or "Pass --force to overwrite" in res_overwrite.stdout
    )

    # 3. Overwrite with --force succeeds
    res_force = runner.invoke(app, ["init", "--force"])
    assert res_force.exit_code == 0
    assert env.config_path.exists()
    assert (env.config_path.stat().st_mode & 0o777) == 0o600


def test_A63_init_minimal(env: EnvPaths, runner: CliRunner) -> None:
    """Test `init --minimal` writes concise configuration."""
    res = runner.invoke(app, ["init", "--minimal"])
    assert res.exit_code == 0
    assert env.config_path.exists()

    content = env.config_path.read_text(encoding="utf-8")
    assert "version: 1" in content
    assert "~/Downloads" in content
    # Minimal config should not include the full example rules
    assert "Invoices" not in content


def test_A63_init_json_mode(env: EnvPaths, runner: CliRunner) -> None:
    """Test `init --json` outputs machine-readable JSON."""
    res = runner.invoke(app, ["--json", "init"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["schema_version"] == 1
    assert data["command"] == "init"
    assert data["status"] == "created"
    assert Path(data["path"]) == env.config_path


def test_A63_init_json_refuses_overwrite(env: EnvPaths, runner: CliRunner) -> None:
    """Test `init --json` error output on existing config."""
    runner.invoke(app, ["init"])
    res = runner.invoke(app, ["--json", "init"])
    assert res.exit_code == 2
    data = json.loads(res.stdout)
    assert data["schema_version"] == 1
    assert data["code"] == 2
    assert "Pass --force to overwrite" in data["error"]
