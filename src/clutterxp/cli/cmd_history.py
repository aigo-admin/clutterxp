"""Command handlers for `clutterxp history` subcommands."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated

import typer

from clutterxp import paths
from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import (
    get_console,
    print_json,
    render_history_table,
    safe_str,
)
from clutterxp.history.service import HistoryService, op_to_dict
from clutterxp.models import OpStatus
from clutterxp.units import parse_duration_seconds

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions

history_app = typer.Typer(
    help="Inspect and maintain file operation history",
    invoke_without_command=True,
)


@history_app.callback(invoke_without_command=True)
def history_list_command(
    ctx: typer.Context,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Maximum records to return"),
    ] = 50,
    batch: Annotated[
        int | None,
        typer.Option("--batch", "-b", help="Filter by batch ID"),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            "-s",
            help="Filter by status (done, undone, failed, pending)",
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Filter operations since duration (e.g. '7d') or ISO timestamp",
        ),
    ] = None,
    rule: Annotated[
        str | None,
        typer.Option("--rule", help="Filter by rule name"),
    ] = None,
    path: Annotated[
        str | None,
        typer.Option("--path", help="Filter by path substring"),
    ] = None,
) -> None:
    """List file operations from the history journal."""
    if ctx.invoked_subcommand is not None:
        return

    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    db_path = paths.history_db_path()

    with handle_errors(json_mode=opts.json_mode, command_name="history"):
        history = HistoryService(db_path)

        op_status = None
        if status:
            try:
                op_status = OpStatus(status.lower())
            except ValueError:
                sys.stderr.write(
                    f"error: Invalid status '{status}'. Choices: done, undone, failed, pending\n"
                )
                raise typer.Exit(code=2) from None

        since_iso = None
        if since:
            try:
                secs = parse_duration_seconds(since)
                dt = datetime.now(timezone.utc) - timedelta(seconds=secs)
                since_iso = dt.isoformat()
            except Exception:
                since_iso = since

        records = history.list_ops(
            batch_id=batch,
            status=op_status,
            limit=limit,
            since_iso=since_iso,
            rule_name=rule,
            path_contains=path,
        )

        if opts.json_mode:
            ops_json = [op_to_dict(r) for r in records]
            print_json({"schema_version": 1, "operations": ops_json})
        else:
            if not records:
                get_console(no_color=opts.no_color).print("No history operations found.")
            else:
                render_history_table(records, no_color=opts.no_color)


@history_app.command("show")
def history_show_command(
    ctx: typer.Context,
    op_id: Annotated[int, typer.Argument(help="Operation ID to inspect")],
) -> None:
    """Show details of a specific operation and its related events."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    db_path = paths.history_db_path()

    with handle_errors(json_mode=opts.json_mode, command_name="history_show"):
        history = HistoryService(db_path)
        op = history.get_op(op_id)
        if op is None:
            sys.stderr.write(f"error: Operation ID {op_id} not found in history\n")
            raise typer.Exit(code=2)

        events = history.event_list(op_id=op_id)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "history_show",
                    "operation": op_to_dict(op),
                    "events": events,
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"[bold]Operation #{op.id}[/bold]")
            console.print(f"  Batch:       {op.batch_id}")
            console.print(f"  Status:      {op.status.value}")
            console.print(f"  Rule:        {op.rule_name or '(none)'}")
            console.print(f"  Strategy:    {op.strategy.value if op.strategy else '(none)'}")
            console.print(f"  Source:      {safe_str(op.src_path)}")
            console.print(f"  Destination: {safe_str(op.dst_path or '(none)')}")
            console.print(f"  Size:        {op.size} bytes")
            console.print(f"  SHA-256:     {op.sha256 or '(none)'}")
            console.print(f"  Created:     {op.created_at}")
            console.print(f"  Finished:    {op.finished_at or '(none)'}")
            console.print(f"  Undone:      {op.undone_at or '(none)'}")
            if op.error:
                console.print(f"  Error:       {op.error}")
            if events:
                console.print(f"\n[bold]Events ({len(events)}):[/bold]")
                for ev in events:
                    lvl = ev.get("level", "info").upper()
                    ts = ev.get("timestamp")
                    msg = ev.get("message")
                    console.print(f"  [{lvl}] {ts}: {msg}")


@history_app.command("prune")
def history_prune_command(
    ctx: typer.Context,
    older_than: Annotated[
        str,
        typer.Option(
            "--older-than",
            help="Prune operations older than duration (e.g. 180d)",
        ),
    ] = "180d",
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt",
        ),
    ] = False,
) -> None:
    """Prune history operations older than a cutoff (deletes rows only, keeps pending ops)."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    db_path = paths.history_db_path()

    with handle_errors(json_mode=opts.json_mode, command_name="history_prune"):
        history = HistoryService(db_path)

        try:
            secs = parse_duration_seconds(older_than)
            days = max(1, secs // 86400)
        except Exception:
            try:
                days = int(older_than.rstrip("dD"))
            except ValueError:
                sys.stderr.write(f"error: Invalid duration '{older_than}'. Expected e.g. '180d'\n")
                raise typer.Exit(code=2) from None

        if (
            not yes
            and not opts.json_mode
            and not typer.confirm(f"Prune history records older than {days} days?", default=False)
        ):
            get_console(no_color=opts.no_color).print("Aborted.")
            raise typer.Exit(code=0)

        pruned = history.prune(days)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "history_prune",
                    "older_than_days": days,
                    "pruned_rows": pruned,
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"Pruned {pruned} history operations older than {days} days.")


__all__ = ["history_app"]
