"""Command handlers for `clutterxp duplicates` subcommands."""

from __future__ import annotations

import csv
import io
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from clutterxp import paths
from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import get_console, print_json, safe_str
from clutterxp.duplicates.detector import DuplicateDetectorImpl
from clutterxp.duplicates.hashcache import HashCache
from clutterxp.duplicates.report import groups_to_csv_rows, groups_to_json
from clutterxp.units import parse_size

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions

duplicates_app = typer.Typer(
    help="Detect duplicate files across directories",
    no_args_is_help=True,
)


@duplicates_app.command("scan")
def duplicates_scan_command(
    ctx: typer.Context,
    paths_arg: Annotated[
        list[Path],
        typer.Argument(
            metavar="PATH...",
            help="One or more directory paths to scan for duplicates",
        ),
    ],
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive/--no-recursive",
            help="Scan directories recursively",
        ),
    ] = True,
    min_size: Annotated[
        str,
        typer.Option(
            "--min-size",
            help="Minimum file size to consider (e.g. 1B, 10KB, 1MB)",
        ),
    ] = "1B",
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            help="Glob pattern to exclude from scan (repeatable)",
        ),
    ] = None,
    jobs: Annotated[
        int,
        typer.Option(
            "--jobs",
            "-j",
            help="Number of parallel hashing threads",
        ),
    ] = min(4, os.cpu_count() or 1),
    no_potential: Annotated[
        bool,
        typer.Option(
            "--no-potential",
            help="Skip potential duplicates stage based on name normalization",
        ),
    ] = False,
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: table | json | csv",
        ),
    ] = "table",
    output_file: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write results to file instead of stdout",
        ),
    ] = None,
) -> None:
    """Scan directories for exact and potential duplicate files."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    cache_path = paths.cache_db_path()

    with handle_errors(json_mode=opts.json_mode, command_name="duplicates_scan"):
        # Validate folders exist
        for p in paths_arg:
            if not p.exists():
                sys.stderr.write(f"error: Directory not found: {p}\n")
                raise typer.Exit(code=2)

        try:
            min_size_bytes = parse_size(min_size)
        except Exception:
            sys.stderr.write(f"error: Invalid min-size '{min_size}'\n")
            raise typer.Exit(code=2) from None

        cache = HashCache(cache_path)
        cancel_event = threading.Event()
        detector = DuplicateDetectorImpl(cache=cache, cancel=cancel_event)

        try:
            groups = detector.scan(
                paths_arg,
                recursive=recursive,
                min_size=min_size_bytes,
                exclude=exclude if exclude else (),
                jobs=jobs,
                potential=not no_potential,
            )
        except (KeyboardInterrupt, InterruptedError):
            if not opts.quiet and not opts.json_mode:
                get_console(no_color=opts.no_color).print("\nScan interrupted. Cache preserved.")
            raise typer.Exit(code=0) from None

        is_json = opts.json_mode or output_format.lower() == "json"
        is_csv = output_format.lower() == "csv"

        if is_json:
            result_json = groups_to_json(
                groups,
                paths_arg,
                detector.last_scanned_files,
                detector.last_scanned_bytes,
            )
            if output_file is not None:
                import json

                output_file.write_text(json.dumps(result_json, indent=2) + "\n", encoding="utf-8")
            else:
                print_json(result_json)
        elif is_csv:
            rows = groups_to_csv_rows(groups)
            out_buf = io.StringIO()
            csv_writer = csv.writer(out_buf)
            csv_writer.writerows(rows)
            csv_text = out_buf.getvalue()

            if output_file is not None:
                output_file.write_text(csv_text, encoding="utf-8")
            else:
                sys.stdout.write(csv_text)
                sys.stdout.flush()
        else:
            # Table format
            console = get_console(no_color=opts.no_color)
            exact_groups = [g for g in groups if g.kind == "exact"]
            hardlink_groups = [g for g in groups if g.kind == "hardlink"]
            potential_groups = [g for g in groups if g.kind == "potential"]

            total_wasted = sum(g.wasted_bytes for g in exact_groups)

            console.print(
                f"[bold]Duplicates Scan Summary[/bold]: {len(exact_groups)} exact groups "
                f"({total_wasted} bytes wasted), {len(potential_groups)} potential groups, "
                f"{len(hardlink_groups)} hardlinks\n"
            )

            if exact_groups:
                table = Table(title="Exact Duplicate Groups", header_style="bold green")
                table.add_column("SHA-256", style="dim")
                table.add_column("Size", justify="right")
                table.add_column("Wasted", justify="right")
                table.add_column("Files (oldest first)")

                for g in exact_groups:
                    sha = (g.sha256 or "")[:12] + "..."
                    size_str = str(g.files[0].size) if g.files else "0"
                    files_str = "\n".join(safe_str(f.path) for f in g.files)
                    table.add_row(sha, size_str, str(g.wasted_bytes), files_str)
                console.print(table)
                console.print()

            if potential_groups:
                table = Table(
                    title="Potential Duplicates (review manually)",
                    header_style="bold yellow",
                )
                table.add_column("Note")
                table.add_column("Files")

                for g in potential_groups:
                    files_str = "\n".join(safe_str(f.path) for f in g.files)
                    table.add_row(g.note, files_str)
                console.print(table)
                console.print()


@duplicates_app.command("cache")
def duplicates_cache_command(
    ctx: typer.Context,
    clear: Annotated[
        bool,
        typer.Option(
            "--clear",
            help="Clear all cached file hashes",
        ),
    ] = False,
) -> None:
    """Manage the duplicate detection hash cache."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    cache_path = paths.cache_db_path()

    with handle_errors(json_mode=opts.json_mode, command_name="duplicates_cache"):
        cache = HashCache(cache_path)
        if clear:
            cache.clear()
            if opts.json_mode:
                print_json(
                    {
                        "schema_version": 1,
                        "command": "duplicates_cache",
                        "status": "cleared",
                        "path": safe_str(cache_path),
                    }
                )
            else:
                console = get_console(no_color=opts.no_color)
                console.print(f"Duplicate hash cache cleared ({safe_str(cache_path)}).")
        else:
            if opts.json_mode:
                print_json(
                    {
                        "schema_version": 1,
                        "command": "duplicates_cache",
                        "path": safe_str(cache_path),
                        "exists": cache_path.exists(),
                    }
                )
            else:
                console = get_console(no_color=opts.no_color)
                console.print(f"Hash cache path: {safe_str(cache_path)}")


__all__ = ["duplicates_app"]
