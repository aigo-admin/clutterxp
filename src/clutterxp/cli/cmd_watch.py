"""Command handler for `clutterxp watch`."""

from __future__ import annotations

import signal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from clutterxp import paths
from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import get_console, print_json, render_plan, safe_str
from clutterxp.models import PlanStatus
from clutterxp.watch.service import WatchService

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions


def watch_command(
    ctx: typer.Context,
    paths_arg: Annotated[
        list[Path] | None,
        typer.Argument(
            metavar="PATH",
            help="Folders to watch (defaults to configured watch folders)",
        ),
    ] = None,
    poll: Annotated[
        bool,
        typer.Option(
            "--poll",
            help="Use polling observer instead of inotify",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "-n",
            "--dry-run",
            help="Run watch service in dry-run mode (log moves without moving)",
        ),
    ] = False,
    once: Annotated[
        bool,
        typer.Option(
            "--once",
            help="Perform a single scan-and-organize pass and exit",
        ),
    ] = False,
) -> None:
    """Run the background directory watcher service."""
    from clutterxp.cli.app import build_organizer, get_globals

    opts: GlobalOptions = get_globals(ctx)
    target_config = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="watch"):
        organizer = build_organizer(target_config)

        if once:
            # Single scan pass
            plan = organizer.plan(paths=paths_arg if paths_arg else None)
            if dry_run:
                if opts.json_mode:
                    print_json(
                        {
                            "schema_version": 1,
                            "command": "watch",
                            "mode": "once",
                            "dry_run": True,
                            "summary": {
                                "will_move": plan.count(PlanStatus.WILL_MOVE)
                                + plan.count(PlanStatus.WILL_MOVE_RENAMED),
                                "unmatched": plan.count(PlanStatus.NO_MATCH)
                                + plan.count(PlanStatus.IGNORED),
                                "ambiguous": plan.count(PlanStatus.AMBIGUOUS),
                                "blocked": plan.count(PlanStatus.BLOCKED),
                            },
                        }
                    )
                else:
                    render_plan(plan, dry_run=True, no_color=opts.no_color)
            else:
                batch_res = organizer.apply(plan, command="watch")
                if opts.json_mode:
                    print_json(
                        {
                            "schema_version": 1,
                            "command": "watch",
                            "mode": "once",
                            "dry_run": False,
                            "batch_id": batch_res.batch_id,
                            "summary": {
                                "moved": batch_res.moved,
                                "skipped": batch_res.skipped,
                                "blocked": batch_res.blocked,
                                "failed": batch_res.failed,
                            },
                        }
                    )
                else:
                    console = get_console(no_color=opts.no_color)
                    console.print(
                        f"Watch pass complete: {batch_res.moved} moved, "
                        f"{batch_res.skipped} skipped, {batch_res.blocked} blocked, "
                        f"{batch_res.failed} failed"
                    )
            return

        # Continuous watch mode
        def on_event_cb(event_data: dict[str, Any]) -> None:
            if opts.json_mode:
                print_json(event_data)
            elif not opts.quiet:
                kind = event_data.get("kind", "")
                p = event_data.get("path", "")
                status = event_data.get("status", "")
                console = get_console(no_color=opts.no_color)
                console.print(f"[{kind}] {safe_str(p)}: {status}")

        service = WatchService(
            config_path=target_config,
            organizer=organizer,
            poll=poll,
            dry_run=dry_run,
            on_event=on_event_cb,
        )

        def sig_handler(signum: int, frame: Any) -> None:
            service.stop()

        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)

        if not opts.quiet and not opts.json_mode:
            console = get_console(no_color=opts.no_color)
            console.print("Watching folders... Press Ctrl+C to stop.")

        try:
            service.run()
        except KeyboardInterrupt:
            service.stop()


__all__ = ["watch_command"]
