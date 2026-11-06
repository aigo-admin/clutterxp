"""Command handlers for `clutterxp service` subcommands."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Annotated, Any

import typer

from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import get_console, print_json

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions

service_app = typer.Typer(
    help="Manage systemd user service for clutterxp watch",
    no_args_is_help=True,
)


def _get_installer() -> Any:
    """Import and return the service_install module, or exit with a helpful message."""
    import importlib

    try:
        return importlib.import_module("clutterxp.service_install")
    except (ImportError, ModuleNotFoundError):
        sys.stderr.write(
            "Service management is not available in this installation "
            "(clutterxp.service_install module not found).\n"
        )
        raise typer.Exit(code=1) from None


@service_app.command("install")
def service_install_command(ctx: typer.Context) -> None:
    """Install and enable systemd user unit for clutterxp watch."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    with handle_errors(json_mode=opts.json_mode, command_name="service_install"):
        installer = _get_installer()
        res = installer.install()
        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "service_install",
                    "status": "installed",
                    "detail": str(res),
                }
            )
        else:
            get_console(no_color=opts.no_color).print("Service installed and enabled.")


@service_app.command("uninstall")
def service_uninstall_command(ctx: typer.Context) -> None:
    """Disable and remove systemd user unit for clutterxp watch."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    with handle_errors(json_mode=opts.json_mode, command_name="service_uninstall"):
        installer = _get_installer()
        res = installer.uninstall()
        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "service_uninstall",
                    "status": "uninstalled",
                    "detail": str(res),
                }
            )
        else:
            get_console(no_color=opts.no_color).print("Service uninstalled.")


@service_app.command("status")
def service_status_command(ctx: typer.Context) -> None:
    """Check status of systemd user service."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    with handle_errors(json_mode=opts.json_mode, command_name="service_status"):
        installer = _get_installer()
        st = installer.status()
        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "service_status",
                    "status": str(st),
                }
            )
        else:
            get_console(no_color=opts.no_color).print(f"Service status: {st}")


@service_app.command("logs")
def service_logs_command(
    ctx: typer.Context,
    lines: Annotated[
        int,
        typer.Option("--lines", "-n", help="Number of log lines to show"),
    ] = 50,
) -> None:
    """View recent logs from the systemd user service."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    with handle_errors(json_mode=opts.json_mode, command_name="service_logs"):
        installer = _get_installer()
        log_content = installer.logs(lines=lines)
        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "service_logs",
                    "lines": lines,
                    "content": str(log_content),
                }
            )
        else:
            sys.stdout.write(str(log_content))
            sys.stdout.flush()


__all__ = ["service_app"]
