"""Tests for `clutterxp gui` command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from clutterxp.cli.app import app


def test_gui_missing_pyside6_hint(runner: CliRunner) -> None:
    with patch("importlib.import_module", side_effect=ImportError("No module named 'PySide6'")):
        res = runner.invoke(app, ["gui"])
        assert res.exit_code == 1
        assert 'Install GUI support with: pip install "clutterxp[gui]"' in res.output


def test_gui_launches_when_installed(runner: CliRunner) -> None:
    mock_gui_module = MagicMock()

    def mock_import(name: str) -> MagicMock:
        if name in ("PySide6", "clutterxp.gui.app"):
            return mock_gui_module
        raise ImportError(name)

    with patch("importlib.import_module", side_effect=mock_import):
        res = runner.invoke(app, ["gui"])
        assert res.exit_code == 0
        mock_gui_module.main.assert_called_once()
