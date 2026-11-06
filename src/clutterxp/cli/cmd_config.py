"""Command handlers for `clutterxp config` subcommands."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from clutterxp import paths
from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import get_console, print_json, safe_str
from clutterxp.config import jsonschema, loader
from clutterxp.models import ConfigError

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions

config_app = typer.Typer(
    help="Manage ClutterXP configuration (path, show, validate, schema, edit, migrate)",
    no_args_is_help=True,
)


@config_app.command("path")
def config_path_command(ctx: typer.Context) -> None:
    """Print the resolved path to config.yaml."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="config_path"):
        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "config_path",
                    "path": safe_str(target),
                    "exists": target.exists(),
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(safe_str(target))


@config_app.command("show")
def config_show_command(ctx: typer.Context) -> None:
    """Print effective configuration settings with paths expanded."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="config_show"):
        cfg = loader.load(target)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "config_show",
                    "config_path": safe_str(target),
                    "version": 1,
                    "settings": cfg.settings.model_dump(),
                    "watch": [w.model_dump() for w in cfg.watch_entries],
                    "rules": [r.model_dump() for r in cfg.all_rules],
                    "is_valid": cfg.is_valid,
                    "errors": list(cfg.errors),
                    "warnings": list(cfg.warnings),
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"[bold]Configuration file:[/bold] {safe_str(target)}\n")
            console.print(f"[bold]Settings:[/bold] {cfg.settings.model_dump()}\n")
            console.print(f"[bold]Watch Folders ({len(cfg.watch_entries)}):[/bold]")
            for w in cfg.watch_entries:
                console.print(f"  - {safe_str(w.path)} (recursive={w.recursive})")
            console.print(f"\n[bold]Rules ({len(cfg.all_rules)}):[/bold]")
            for r in cfg.all_rules:
                status = "enabled" if r.enabled else "disabled"
                dest_str = safe_str(r.destination)
                console.print(
                    f"  - {safe_str(r.name)} [{status}, priority={r.priority}] -> {dest_str}"
                )


@config_app.command("validate")
def config_validate_command(
    ctx: typer.Context,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Treat warnings as errors (exit code 3)",
        ),
    ] = False,
) -> None:
    """Validate configuration file and print errors/warnings with YAML paths."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="config_validate"):
        cfg = loader.load(target)

        is_valid = len(cfg.errors) == 0 and (not strict or len(cfg.warnings) == 0)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "config_validate",
                    "config_path": safe_str(target),
                    "valid": is_valid,
                    "errors": list(cfg.errors),
                    "warnings": list(cfg.warnings),
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            for err in cfg.errors:
                console.print(f"error: {err}")
            for warn in cfg.warnings:
                console.print(f"warning: {warn}")

            if is_valid and not cfg.errors and not cfg.warnings:
                console.print("Configuration is valid.")

        if cfg.errors:
            raise typer.Exit(code=2)
        if strict and cfg.warnings:
            raise typer.Exit(code=3)


@config_app.command("schema")
def config_schema_command(ctx: typer.Context) -> None:
    """Print the JSON Schema for configuration files."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    with handle_errors(json_mode=opts.json_mode, command_name="config_schema"):
        schema = jsonschema.config_json_schema()
        print_json(schema)


@config_app.command("edit")
def config_edit_command(ctx: typer.Context) -> None:
    """Open $EDITOR on a temp copy, validate on exit, replace atomically if valid."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="config_edit"):
        if not target.exists():
            sys.stderr.write(f"error: Config file not found at '{target}'. Run `init` first.\n")
            raise typer.Exit(code=2)

        # Create temporary working copy in same directory
        temp_fd, temp_path_str = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".edit.tmp"
        )
        temp_path = Path(temp_path_str)
        try:
            with os.fdopen(temp_fd, "wb") as f_out, target.open("rb") as f_in:
                shutil.copyfileobj(f_in, f_out)

            editor = os.environ.get("EDITOR")
            if not editor:
                editor = "nano" if shutil.which("nano") else "vi"

            res = subprocess.run([editor, str(temp_path)], check=False)  # noqa: S603
            if res.returncode != 0:
                sys.stderr.write(f"error: Editor exited with code {res.returncode}\n")
                raise typer.Exit(code=2)

            # Validate the edited file
            try:
                cfg = loader.load(temp_path)
                if not cfg.is_valid:
                    sys.stderr.write("error: Validation failed for edited configuration:\n")
                    for err in cfg.errors:
                        sys.stderr.write(f"  {err}\n")
                    sys.stderr.write("Changes discarded.\n")
                    raise typer.Exit(code=2)
            except ConfigError as ce:
                sys.stderr.write(
                    f"error: Failed to load edited configuration: {ce}\nChanges discarded.\n"
                )
                raise typer.Exit(code=2) from None

            # Backup existing and replace atomically
            bak_path = target.with_name(target.name + ".bak")
            shutil.copy2(target, bak_path)
            with contextlib.suppress(OSError):
                bak_path.chmod(0o600)

            temp_path.chmod(0o600)
            temp_path.replace(target)

            if opts.json_mode:
                print_json(
                    {
                        "schema_version": 1,
                        "command": "config_edit",
                        "status": "updated",
                        "path": safe_str(target),
                    }
                )
            else:
                console = get_console(no_color=opts.no_color)
                console.print(f"Configuration successfully updated: {safe_str(target)}")
        finally:
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()


@config_app.command("migrate")
def config_migrate_command(ctx: typer.Context) -> None:
    """Migrate configuration file if necessary (v1 is current)."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    with handle_errors(json_mode=opts.json_mode, command_name="config_migrate"):
        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "config_migrate",
                    "status": "up_to_date",
                    "version": 1,
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print("Configuration is up to date (version 1).")


__all__ = ["config_app"]
