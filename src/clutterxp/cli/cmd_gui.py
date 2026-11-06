"""Command handler for `clutterxp gui`."""

from __future__ import annotations

import importlib
import sys

import typer


def gui_command(ctx: typer.Context) -> None:
    """Launch the graphical interface (v1.1 feature)."""
    try:
        importlib.import_module("PySide6")
        gui_app = importlib.import_module("clutterxp.gui.app")
        gui_app.main()
    except (ImportError, ModuleNotFoundError, AttributeError):
        sys.stderr.write('Install GUI support with: pip install "clutterxp[gui]"\n')
        raise typer.Exit(code=1) from None


__all__ = ["gui_command"]
