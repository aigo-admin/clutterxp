"""Command handler for `clutterxp organize`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import (
    get_console,
    print_json,
    render_organize_summary,
    render_plan,
    safe_str,
)
from clutterxp.models import (
    ErrorCode,
    Plan,
    PlannedAction,
    PlanStatus,
    SafetyRefusal,
)

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions


def organize_command(
    ctx: typer.Context,
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Folders or files to organize (defaults to configured watch folders)",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "-n",
            "--dry-run",
            help="Preview actions without moving any files",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "-y",
            "--yes",
            help="Skip confirmation prompt for moves exceeding confirm_threshold",
        ),
    ] = False,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive",
            "-r",
            help="Scan directories recursively",
        ),
    ] = False,
    rule: Annotated[
        list[str] | None,
        typer.Option(
            "--rule",
            help="Limit execution to named rules (repeatable)",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            "-l",
            help="Cap maximum number of files to move in this run",
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit with code 3 if any file is ambiguous or failed",
        ),
    ] = False,
) -> None:
    """Sort files into destinations according to configured rules."""
    from clutterxp.cli.app import build_organizer, get_globals

    opts: GlobalOptions = get_globals(ctx)

    with handle_errors(json_mode=opts.json_mode, command_name="organize"):
        organizer = build_organizer(opts.config)
        plan = organizer.plan(
            paths=paths if paths else None,
            recursive=recursive,
            only_rules=rule if rule else None,
        )

        # Apply limit if specified
        if limit is not None and limit >= 0:
            move_count = 0
            limited_actions: list[PlannedAction] = []
            for a in plan.actions:
                if a.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED):
                    if move_count < limit:
                        move_count += 1
                        limited_actions.append(a)
                    else:
                        limited_actions.append(
                            PlannedAction(
                                file=a.file,
                                status=PlanStatus.IGNORED,
                                decision=a.decision,
                                code=ErrorCode.NONE,
                                message="Skipped due to --limit",
                            )
                        )
                else:
                    limited_actions.append(a)
            plan = Plan(folders=plan.folders, actions=tuple(limited_actions))

        moves_planned = plan.count(PlanStatus.WILL_MOVE) + plan.count(PlanStatus.WILL_MOVE_RENAMED)
        ambig_count = plan.count(PlanStatus.AMBIGUOUS)

        # Print plan unless in quiet mode
        if not opts.quiet and not opts.json_mode:
            render_plan(
                plan,
                dry_run=dry_run,
                no_color=opts.no_color,
            )

        # Dry run stops here
        if dry_run:
            if opts.json_mode:
                actions_json = _build_actions_json(plan)
                print_json(
                    {
                        "schema_version": 1,
                        "command": "organize",
                        "dry_run": True,
                        "batch_id": None,
                        "summary": {
                            "moved": 0,
                            "unmatched": plan.count(PlanStatus.NO_MATCH)
                            + plan.count(PlanStatus.IGNORED),
                            "ambiguous": ambig_count,
                            "blocked": plan.count(PlanStatus.BLOCKED),
                            "failed": 0,
                        },
                        "actions": actions_json,
                    }
                )
            if strict and ambig_count > 0:
                raise typer.Exit(code=3)
            return

        # Check confirmation threshold
        threshold = organizer.config.settings.confirm_threshold
        if organizer.needs_confirmation(plan) and not yes:
            if not sys.stdin.isatty():
                raise SafetyRefusal(
                    f"Refused: confirmation required to move {moves_planned} files "
                    f"(confirm_threshold={threshold}) in non-interactive session. "
                    "Pass --yes to confirm."
                )
            if not typer.confirm(f"Move {moves_planned} files?", default=True):
                get_console(no_color=opts.no_color).print("Aborted.")
                raise typer.Exit(code=0)

        # Apply plan
        batch_result = organizer.apply(plan, command="organize")

        if opts.json_mode:
            actions_json = _build_actions_json(plan)
            print_json(
                {
                    "schema_version": 1,
                    "command": "organize",
                    "dry_run": False,
                    "batch_id": batch_result.batch_id,
                    "summary": {
                        "moved": batch_result.moved,
                        "unmatched": plan.count(PlanStatus.NO_MATCH)
                        + plan.count(PlanStatus.IGNORED),
                        "ambiguous": ambig_count,
                        "blocked": batch_result.blocked,
                        "failed": batch_result.failed,
                    },
                    "actions": actions_json,
                }
            )
        else:
            render_organize_summary(plan, batch_result, no_color=opts.no_color)

        # Exit code: 3 on partial failure or strict with ambiguity
        if batch_result.blocked > 0 or batch_result.failed > 0:
            raise typer.Exit(code=3)
        if strict and ambig_count > 0:
            raise typer.Exit(code=3)


def _build_actions_json(plan: Plan) -> list[dict[str, Any]]:
    """Build list of action dictionaries for JSON output."""
    actions_json: list[dict[str, Any]] = []
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
    return actions_json


__all__ = ["organize_command"]
