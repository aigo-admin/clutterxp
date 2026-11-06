"""Command handler for `clutterxp scan`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import print_json, render_plan, safe_str
from clutterxp.models import PlanStatus

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions


def scan_command(
    ctx: typer.Context,
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Folders or files to scan (defaults to configured watch folders)",
        ),
    ] = None,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive",
            "-r",
            help="Scan directories recursively",
        ),
    ] = False,
    show: Annotated[
        str,
        typer.Option(
            "--show",
            help="Filter plan output: all | matched | unmatched | ambiguous | blocked",
        ),
    ] = "all",
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit with code 3 if any file is ambiguous",
        ),
    ] = False,
) -> None:
    """Scan folders, classify files, and preview sorting decisions without modifying anything."""
    from clutterxp.cli.app import build_organizer, get_globals

    opts: GlobalOptions = get_globals(ctx)

    with handle_errors(json_mode=opts.json_mode, command_name="scan"):
        organizer = build_organizer(opts.config)
        plan = organizer.plan(paths=paths if paths else None, recursive=recursive)

        matched_count = plan.count(PlanStatus.WILL_MOVE) + plan.count(PlanStatus.WILL_MOVE_RENAMED)
        unmatched_count = plan.count(PlanStatus.NO_MATCH) + plan.count(PlanStatus.IGNORED)
        ambig_count = plan.count(PlanStatus.AMBIGUOUS)
        blocked_count = plan.count(PlanStatus.BLOCKED)

        if opts.json_mode:
            actions_json = []
            for a in plan.actions:
                status_str = a.status.value
                act_data: dict[str, Any] = {
                    "status": status_str,
                    "src": safe_str(a.file.path),
                }
                if a.dest_path is not None:
                    act_data["dst"] = safe_str(a.dest_path)
                if a.decision.hit is not None:
                    act_data["rule"] = a.decision.hit.rule_name
                    act_data["matched_by"] = a.decision.hit.matched_by
                if a.decision.candidates:
                    act_data["candidates"] = [c.rule_name for c in a.decision.candidates]
                if a.status == PlanStatus.BLOCKED:
                    act_data["code"] = a.code.value
                    act_data["message"] = a.message or a.code.value
                actions_json.append(act_data)

            print_json(
                {
                    "schema_version": 1,
                    "command": "scan",
                    "dry_run": True,
                    "summary": {
                        "matched": matched_count,
                        "unmatched": unmatched_count,
                        "ambiguous": ambig_count,
                        "blocked": blocked_count,
                    },
                    "actions": actions_json,
                }
            )
        else:
            render_plan(
                plan,
                dry_run=True,
                show=show.lower(),
                no_color=opts.no_color,
            )

        if strict and ambig_count > 0:
            raise typer.Exit(code=3)


__all__ = ["scan_command"]
