"""Tests for `clutterxp service` commands proving criterion A66."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clutterxp.cli.app import app


def test_A66_service_missing_module(runner: CliRunner) -> None:
    """When service_install is not available, service commands fail gracefully."""
    with patch(
        "importlib.import_module",
        side_effect=ModuleNotFoundError("No module named 'clutterxp.service_install'"),
    ):
        res = runner.invoke(app, ["service", "status"])
        assert res.exit_code == 1
        assert "Service management is not available in this installation" in res.output


def test_A66_service_install(runner: CliRunner) -> None:
    mock_installer = MagicMock()
    mock_installer.install.return_value = (
        "Installed /home/user/.config/systemd/user/clutterxp.service"
    )

    with patch("clutterxp.cli.cmd_service._get_installer", return_value=mock_installer):
        # Text mode
        res = runner.invoke(app, ["service", "install"])
        assert res.exit_code == 0
        assert "Service installed and enabled" in res.output
        mock_installer.install.assert_called_once()

        # JSON mode
        mock_installer.reset_mock()
        res_json = runner.invoke(app, ["--json", "service", "install"])
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["command"] == "service_install"
        assert data["status"] == "installed"
        assert "clutterxp.service" in data["detail"]


def test_A66_service_uninstall(runner: CliRunner) -> None:
    mock_installer = MagicMock()
    mock_installer.uninstall.return_value = "Removed clutterxp.service"

    with patch("clutterxp.cli.cmd_service._get_installer", return_value=mock_installer):
        # Text mode
        res = runner.invoke(app, ["service", "uninstall"])
        assert res.exit_code == 0
        assert "Service uninstalled" in res.output

        # JSON mode
        res_json = runner.invoke(app, ["--json", "service", "uninstall"])
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["command"] == "service_uninstall"
        assert data["status"] == "uninstalled"


def test_A66_service_status(runner: CliRunner) -> None:
    mock_installer = MagicMock()
    mock_installer.status.return_value = "active (running)"

    with patch("clutterxp.cli.cmd_service._get_installer", return_value=mock_installer):
        res = runner.invoke(app, ["service", "status"])
        assert res.exit_code == 0
        assert "Service status: active (running)" in res.output

        res_json = runner.invoke(app, ["--json", "service", "status"])
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["command"] == "service_status"
        assert data["status"] == "active (running)"


def test_A66_service_logs(runner: CliRunner) -> None:
    mock_installer = MagicMock()
    mock_installer.logs.return_value = "line 1\nline 2\n"

    with patch("clutterxp.cli.cmd_service._get_installer", return_value=mock_installer):
        res = runner.invoke(app, ["service", "logs", "--lines", "10"])
        assert res.exit_code == 0
        assert "line 1" in res.output
        mock_installer.logs.assert_called_with(lines=10)

        res_json = runner.invoke(app, ["--json", "service", "logs", "-n", "10"])
        assert res_json.exit_code == 0
        data = json.loads(res_json.output)
        assert data["command"] == "service_logs"
        assert data["lines"] == 10
        assert "line 1" in data["content"]
