"""Command handler for `clutterxp undo`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import get_console, print_json, safe_str
from clutterxp.models import OpStatus, SafetyRefusal

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions


def undo_command(
    ctx: typer.Context,
    op: Annotated[
        int | None,
        typer.Option("--op", help="Undo a single operation by ID"),
    ] = None,
    batch: Annotated[
        int | None,
        typer.Option("--batch", "-b", help="Undo all operations in a batch"),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            help="Undo the last operation that moved this destination file",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "-n",
            "--dry-run",
            help="Check undo preconditions without modifying files",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "-y",
            "--yes",
            help="Skip confirmation prompt for undoing the last batch",
        ),
    ] = False,
    on_conflict: Annotated[
        str,
        typer.Option(
            "--on-conflict",
            help="Origin collision behavior: skip | rename",
        ),
    ] = "skip",
    allow_modified: Annotated[
        bool,
        typer.Option(
            "--allow-modified",
            help="Allow undo even if destination file size or mtime has changed",
        ),
    ] = False,
) -> None:
    """Safely undo previous file operations under journal-first protocol."""
    from clutterxp.cli.app import build_organizer, get_globals

    opts: GlobalOptions = get_globals(ctx)

    if on_conflict not in ("skip", "rename"):
        sys.stderr.write(
            f"error: Invalid --on-conflict value '{on_conflict}'. Must be 'skip' or 'rename'.\n"
        )
        raise typer.Exit(code=2)

    with handle_errors(json_mode=opts.json_mode, command_name="undo"):
        organizer = build_organizer(opts.config)

        # Default selector: last batch
        target_batch = batch
        if op is None and batch is None and file is None:
            last_batch = organizer.history.last_batch_id()
            if last_batch is None:
                if opts.json_mode:
                    print_json(
                        {
                            "schema_version": 1,
                            "command": "undo",
                            "dry_run": dry_run,
                            "outcomes": [],
                        }
                    )
                else:
                    get_console(no_color=opts.no_color).print("No batches found to undo.")
                return

            ops = organizer.history.list_ops(batch_id=last_batch, status=OpStatus.DONE)
            if not ops:
                if opts.json_mode:
                    print_json(
                        {
                            "schema_version": 1,
                            "command": "undo",
                            "dry_run": dry_run,
                            "outcomes": [],
                        }
                    )
                else:
                    get_console(no_color=opts.no_color).print(
                        f"Batch #{last_batch} has no completed operations to undo."
                    )
                return

            if not yes and not dry_run and not opts.json_mode:
                if not sys.stdin.isatty():
                    raise SafetyRefusal(
                        f"Refused: confirmation required to undo last batch #{last_batch} "
                        f"({len(ops)} files) in non-interactive session. Pass --yes to confirm."
                    )
                confirmed = typer.confirm(
                    f"Undo last batch #{last_batch}: {len(ops)} files?", default=True
                )
                if not confirmed:
                    get_console(no_color=opts.no_color).print("Aborted.")
                    raise typer.Exit(code=0)

            target_batch = last_batch

        outcomes = organizer.undo_service.undo(
            op_id=op,
            batch_id=target_batch,
            file_path=file,
            dry_run=dry_run,
            on_conflict=on_conflict,
            allow_modified=allow_modified,
        )

        success_count = sum(1 for o in outcomes if o.ok)
        refused_count = len(outcomes) - success_count

        if opts.json_mode:
            outcomes_json = [
                {
                    "op_id": o.op_id,
                    "ok": o.ok,
                    "restored_to": safe_str(o.restored_to) if o.restored_to else None,
                    "code": o.code.value,
                    "message": o.message,
                }
                for o in outcomes
            ]
            print_json(
                {
                    "schema_version": 1,
                    "command": "undo",
                    "dry_run": dry_run,
                    "outcomes": outcomes_json,
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            prefix = r"\[dry-run] " if dry_run else ""
            for o in outcomes:
                if o.ok:
                    target_str = safe_str(o.restored_to) if o.restored_to else ""
                    console.print(f" {prefix}RESTORE  op #{o.op_id} -> {target_str}")
                else:
                    console.print(f" {prefix}REFUSE   op #{o.op_id} ({o.code.value}): {o.message}")

            console.print(
                f"\n{prefix}Summary: {success_count} restored, {refused_count} refused/failed"
            )

        if refused_count > 0:
            raise typer.Exit(code=3)


__all__ = ["undo_command"]
