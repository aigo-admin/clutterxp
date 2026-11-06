"""Typer root application, global option parsing, dependency wiring, and entry points."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from clutterxp import logging_setup, paths, safety
from clutterxp.cli.cmd_config import config_app
from clutterxp.cli.cmd_doctor import doctor_command
from clutterxp.cli.cmd_duplicates import duplicates_app
from clutterxp.cli.cmd_gui import gui_command
from clutterxp.cli.cmd_history import history_app
from clutterxp.cli.cmd_init import init_command
from clutterxp.cli.cmd_organize import organize_command
from clutterxp.cli.cmd_rules import rules_app
from clutterxp.cli.cmd_scan import scan_command
from clutterxp.cli.cmd_service import service_app
from clutterxp.cli.cmd_undo import undo_command
from clutterxp.cli.cmd_watch import watch_command
from clutterxp.cli.errors import handle_errors
from clutterxp.config import loader
from clutterxp.history.service import HistoryService
from clutterxp.history.undo import UndoService
from clutterxp.models import ClutterError, ConfigError, ErrorCode, SafetyRefusal
from clutterxp.mover.safe_move import SafeMover
from clutterxp.organizer import OrganizerImpl
from clutterxp.rules import build_engine

logger = logging.getLogger("clutterxp.cli")


@dataclass(frozen=True, slots=True)
class GlobalOptions:
    """Parsed global command-line options passed down through Typer context."""

    config: Path | None
    json_mode: bool
    quiet: bool
    verbose: int
    no_color: bool
    allow_root: bool


def get_globals(ctx: typer.Context) -> GlobalOptions:
    """Retrieve GlobalOptions from Typer context or fallback to defaults."""
    if ctx.obj is not None and isinstance(ctx.obj, GlobalOptions):
        return ctx.obj
    obj = ctx.find_object(GlobalOptions)
    if obj is not None:
        return obj
    return GlobalOptions(
        config=None,
        json_mode=False,
        quiet=False,
        verbose=0,
        no_color=False,
        allow_root=False,
    )


def version_callback(value: bool) -> None:
    """Print version and exit cleanly."""
    if value:
        try:
            from clutterxp import __version__

            ver = __version__
        except Exception:
            ver = "1.0.0"
        typer.echo(f"clutterxp {ver}")
        raise typer.Exit(code=0)


def build_organizer(config_path: Path | None = None) -> OrganizerImpl:
    """Wire dependencies and build an OrganizerImpl facade."""
    resolved_config_path = config_path if config_path is not None else paths.config_path()
    runtime_cfg = loader.load(resolved_config_path)

    engine = build_engine(
        list(runtime_cfg.rules),
        runtime_cfg.settings,
        watch=list(runtime_cfg.watch_entries),
    )
    mover = SafeMover(verify_copy=runtime_cfg.settings.verify_copy)
    history = HistoryService(paths.history_db_path())
    undo_svc = UndoService(history, mover, paths.fs_lock_path())

    return OrganizerImpl(
        runtime_cfg,
        engine,
        mover,
        history,
        undo_service=undo_svc,
        fs_lock_path=paths.fs_lock_path(),
    )


# Root application with completion support
app = typer.Typer(
    name="clutterxp",
    help="ClutterXP: Configurable, safe, fast file organizer.",
    no_args_is_help=True,
    add_completion=True,
)


@app.callback(no_args_is_help=True)
def main_callback(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            envvar="CLUTTERXP_CONFIG",
            help="Path to configuration file",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Machine-readable JSON output (schema_version: 1); no colors, no prompts",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "-q",
            "--quiet",
            help="Quiet output (errors and results only)",
        ),
    ] = False,
    verbose: Annotated[
        int,
        typer.Option(
            "-v",
            "--verbose",
            count=True,
            help="Increase logging verbosity (-v for INFO, -vv for DEBUG)",
        ),
    ] = 0,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color",
            help="Disable colors (also honors NO_COLOR environment variable)",
        ),
    ] = False,
    allow_root: Annotated[
        bool,
        typer.Option(
            "--allow-root",
            help="Permit running as root user (otherwise refused for safety)",
        ),
    ] = False,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Show ClutterXP version",
        ),
    ] = None,
) -> None:
    """ClutterXP root callback setting up globals, logging, and safety checks."""
    effective_no_color = no_color or bool(os.environ.get("NO_COLOR", "").strip())
    ctx.obj = GlobalOptions(
        config=config,
        json_mode=json_output,
        quiet=quiet,
        verbose=verbose,
        no_color=effective_no_color,
        allow_root=allow_root,
    )

    # Invariant S10: refuse root execution unless --allow-root
    with handle_errors(json_mode=json_output, command_name="root"):
        safety.refuse_root(allow_root=allow_root)

    # Setup logging
    log_level = 0
    if quiet:
        log_level = 0
    elif verbose > 0:
        log_level = verbose
    logging_setup.setup_logging(verbosity=log_level)


# Register subcommands and command groups
app.command("init")(init_command)
app.add_typer(config_app, name="config")
app.add_typer(rules_app, name="rules")
app.command("scan")(scan_command)
app.command("organize")(organize_command)
app.command("watch")(watch_command)
app.add_typer(history_app, name="history")
app.command("undo")(undo_command)
app.add_typer(duplicates_app, name="duplicates")
app.add_typer(service_app, name="service")
app.command("doctor")(doctor_command)
app.command("gui")(gui_command)


def main() -> None:
    """CLI entry point for clutterxp binary."""
    try:
        app()
    except SafetyRefusal as e:
        sys.stderr.write(f"error: {e}\n")
        sys.exit(4)
    except ConfigError as e:
        if e.problems:
            for p in e.problems:
                sys.stderr.write(f"error: {p}\n")
        else:
            sys.stderr.write(f"error: {e}\n")
        sys.exit(2)
    except ClutterError as e:
        code = 5 if e.code == ErrorCode.LOCK_TIMEOUT or "lock" in str(e).lower() else 3
        sys.stderr.write(f"error: {e}\n")
        sys.exit(code)
    except SystemExit:
        raise
    except Exception as e:
        logger.exception("Unexpected error")
        sys.stderr.write(f"error: unexpected error: {e}\n")
        sys.exit(1)


def cxp_main() -> None:
    """CLI entry point for cxp binary alias."""
    main()


__all__ = [
    "GlobalOptions",
    "app",
    "build_organizer",
    "cxp_main",
    "get_globals",
    "main",
]
