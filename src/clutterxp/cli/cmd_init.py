"""Command handler for `clutterxp init`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from clutterxp import paths
from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import get_console, print_json, safe_str
from clutterxp.config import writer

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions


def init_command(
    ctx: typer.Context,
    minimal: Annotated[
        bool,
        typer.Option(
            "--minimal",
            help="Write minimal configuration with Downloads watch only",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite existing configuration file",
        ),
    ] = False,
) -> None:
    """Initialize a commented config.yaml file with 0600 permissions."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="init"):
        if target.exists() and not force:
            msg = f"Config file already exists at '{target}'. Pass --force to overwrite."
            if opts.json_mode:
                print_json(
                    {
                        "schema_version": 1,
                        "command": "init",
                        "error": msg,
                        "code": 2,
                    }
                )
            else:
                sys.stderr.write(f"error: {msg}\n")
            raise typer.Exit(code=2)

        created_path: Path = writer.init_config(target, minimal=minimal, force=force)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "init",
                    "path": safe_str(created_path),
                    "status": "created",
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"Created configuration at {safe_str(created_path)} (mode 0600)")


__all__ = ["init_command"]
