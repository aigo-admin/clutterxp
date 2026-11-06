"""Test A90: GUI optional dependency, pyproject configuration, and clean install hints."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import tomllib  # type: ignore[import-not-found]
from typer.testing import CliRunner

from clutterxp.cli.app import app
from clutterxp.gui.app import main


def test_A90_gui_extra_and_missing_dependency_hint() -> None:
    # 1. Verify pyproject.toml configuration
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert pyproject_path.exists()

    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    # Check optional-dependencies
    optional_deps = data.get("project", {}).get("optional-dependencies", {})
    assert "gui" in optional_deps, "Expected 'gui' in project.optional-dependencies"
    gui_deps = optional_deps["gui"]
    assert any("pyside6" in dep.lower() for dep in gui_deps)

    # Check gui-scripts
    gui_scripts = data.get("project", {}).get("gui-scripts", {})
    assert "clutterxp-gui" in gui_scripts, "Expected 'clutterxp-gui' in project.gui-scripts"
    assert gui_scripts["clutterxp-gui"] == "clutterxp.gui.app:main"

    # Verify entry point main function exists and is callable
    assert callable(main)

    # 2. Verify missing PySide6 output gives clear install instruction
    runner = CliRunner()

    with patch.dict(sys.modules, {"PySide6": None}):
        result = runner.invoke(app, ["gui"])
        assert result.exit_code == 1
        assert 'pip install "clutterxp[gui]"' in result.stderr
