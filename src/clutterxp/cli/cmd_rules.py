"""Command handlers for `clutterxp rules` subcommands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from clutterxp import paths
from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import (
    get_console,
    print_json,
    render_rule_trace,
    render_rules_table,
    safe_str,
)
from clutterxp.config import loader, writer
from clutterxp.models import CollisionPolicy, FileInfo, MatchMode
from clutterxp.rules import build_engine
from clutterxp.rules.tokenizer import camel_tokens, raw_tokens

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions

rules_app = typer.Typer(
    help="Manage and test file organization rules",
    no_args_is_help=True,
)


@rules_app.command("list")
def rules_list_command(
    ctx: typer.Context,
    all_rules: Annotated[
        bool,
        typer.Option(
            "--all",
            "-a",
            help="Include disabled rules in the output",
        ),
    ] = False,
) -> None:
    """List configured rules in priority order."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_list"):
        cfg = loader.load(target)
        rules_to_show = cfg.all_rules if all_rules else cfg.rules

        if opts.json_mode:
            rules_data = []
            for r in rules_to_show:
                data = {
                    "name": r.name,
                    "priority": r.priority,
                    "enabled": r.enabled,
                    "keywords": list(r.keywords),
                    "all_of": list(r.all_of),
                    "exclude": list(r.exclude),
                    "extensions": list(r.extensions),
                    "destination": safe_str(r.destination),
                    "collision": r.collision.value
                    if r.collision is not None
                    else CollisionPolicy.SKIP.value,
                    "match": r.match.value if r.match is not None else MatchMode.TOKEN.value,
                }
                rules_data.append(data)
            print_json({"schema_version": 1, "rules": rules_data})
        else:
            render_rules_table(rules_to_show, no_color=opts.no_color)


@rules_app.command("show")
def rules_show_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Name of the rule to inspect")],
) -> None:
    """Show details of a specific rule."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_show"):
        cfg = loader.load(target)
        found = None
        for r in cfg.all_rules:
            if r.name.casefold() == name.casefold():
                found = r
                break

        if found is None:
            sys.stderr.write(f"error: Rule '{name}' not found\n")
            raise typer.Exit(code=2)

        data = {
            "name": found.name,
            "priority": found.priority,
            "enabled": found.enabled,
            "keywords": list(found.keywords),
            "all_of": list(found.all_of),
            "exclude": list(found.exclude),
            "extensions": list(found.extensions),
            "destination": safe_str(found.destination),
            "collision": found.collision.value
            if found.collision is not None
            else CollisionPolicy.SKIP.value,
            "match": found.match.value if found.match is not None else MatchMode.TOKEN.value,
        }
        if found.conditions:
            data["conditions"] = found.conditions.model_dump()

        if opts.json_mode:
            print_json({"schema_version": 1, "rule": data})
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"[bold]Rule:[/bold] {found.name}")
            console.print(f"  Priority:    {found.priority}")
            console.print(f"  Enabled:     {found.enabled}")
            console.print(f"  Keywords:    {', '.join(found.keywords) or '(none)'}")
            console.print(f"  All-of:      {', '.join(found.all_of) or '(none)'}")
            console.print(f"  Exclude:     {', '.join(found.exclude) or '(none)'}")
            console.print(f"  Extensions:  {', '.join(found.extensions) or '(none)'}")
            console.print(f"  Destination: {safe_str(found.destination)}")
            coll_val = (
                found.collision.value if found.collision is not None else CollisionPolicy.SKIP.value
            )
            match_val = found.match.value if found.match is not None else MatchMode.TOKEN.value
            console.print(f"  Collision:   {coll_val}")
            console.print(f"  Match mode:  {match_val}")


@rules_app.command("add")
def rules_add_command(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Rule name"),
    ] = None,
    keyword: Annotated[
        list[str] | None,
        typer.Option("--keyword", "-k", help="Keyword (repeatable)"),
    ] = None,
    all_of: Annotated[
        list[str] | None,
        typer.Option("--all-of", help="Required keyword (repeatable)"),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Excluded keyword (repeatable)"),
    ] = None,
    ext: Annotated[
        list[str] | None,
        typer.Option("--ext", "-e", help="Extension filter (repeatable)"),
    ] = None,
    dest: Annotated[
        str | None,
        typer.Option("--dest", "-d", help="Destination path template"),
    ] = None,
    priority: Annotated[
        int,
        typer.Option("--priority", "-p", help="Priority (lower runs first)"),
    ] = 20,
    collision: Annotated[
        CollisionPolicy,
        typer.Option("--collision", help="Collision resolution policy"),
    ] = CollisionPolicy.SKIP,
    match: Annotated[
        MatchMode,
        typer.Option("--match", "-m", help="Keyword matching mode"),
    ] = MatchMode.TOKEN,
) -> None:
    """Add a new rule to config.yaml (prompts interactively for missing values)."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_add"):
        # Interactive prompts if required values are missing
        rule_name = name
        if not rule_name:
            if opts.json_mode:
                sys.stderr.write("error: Missing required --name in --json mode\n")
                raise typer.Exit(code=2)
            rule_name = typer.prompt("Rule name")

        rule_dest = dest
        if not rule_dest:
            if opts.json_mode:
                sys.stderr.write("error: Missing required --dest in --json mode\n")
                raise typer.Exit(code=2)
            rule_dest = typer.prompt("Destination directory template")

        keywords = list(keyword) if keyword else []
        extensions = list(ext) if ext else []

        if not keywords and not extensions:
            if opts.json_mode:
                sys.stderr.write(
                    "error: Rule must have at least one keyword or extension in --json mode\n"
                )
                raise typer.Exit(code=2)
            kw_input = typer.prompt("Keywords (space or comma-separated)", default="")
            if kw_input:
                keywords = [k.strip() for k in kw_input.replace(",", " ").split() if k.strip()]

        rule_dict: dict[str, Any] = {
            "name": rule_name,
            "destination": rule_dest,
            "priority": priority,
            "collision": collision.value,
            "match": match.value,
            "keywords": keywords,
            "all_of": list(all_of) if all_of else [],
            "exclude": list(exclude) if exclude else [],
            "extensions": extensions,
        }

        writer.add_rule(target, rule_dict)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "rules_add",
                    "status": "added",
                    "rule": rule_dict,
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"Rule '{rule_name}' added successfully to {safe_str(target)}")


@rules_app.command("edit")
def rules_edit_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Rule name to edit")],
    dest: Annotated[
        str | None,
        typer.Option("--dest", "-d", help="New destination"),
    ] = None,
    priority: Annotated[
        int | None,
        typer.Option("--priority", "-p", help="New priority"),
    ] = None,
    collision: Annotated[
        CollisionPolicy | None,
        typer.Option("--collision", help="New collision policy"),
    ] = None,
    match: Annotated[
        MatchMode | None,
        typer.Option("--match", "-m", help="New match mode"),
    ] = None,
) -> None:
    """Edit fields of an existing rule."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_edit"):
        updates: dict[str, Any] = {}
        if dest is not None:
            updates["destination"] = dest
        if priority is not None:
            updates["priority"] = priority
        if collision is not None:
            updates["collision"] = collision.value
        if match is not None:
            updates["match"] = match.value

        if not updates:
            sys.stderr.write("error: No fields provided to edit\n")
            raise typer.Exit(code=2)

        writer.edit_rule(target, name, updates)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "rules_edit",
                    "name": name,
                    "status": "updated",
                    "updates": updates,
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"Rule '{name}' updated successfully in {safe_str(target)}")


@rules_app.command("remove")
def rules_remove_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Rule name to remove")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt"),
    ] = False,
) -> None:
    """Remove a rule from config.yaml."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_remove"):
        if (
            not yes
            and not opts.json_mode
            and not typer.confirm(f"Remove rule '{name}'?", default=False)
        ):
            get_console(no_color=opts.no_color).print("Aborted.")
            raise typer.Exit(code=0)

        writer.remove_rule(target, name)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "rules_remove",
                    "name": name,
                    "status": "removed",
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"Rule '{name}' removed successfully from {safe_str(target)}")


@rules_app.command("enable")
def rules_enable_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Rule name to enable")],
) -> None:
    """Enable a rule in config.yaml."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_enable"):
        writer.enable_rule(target, name)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "rules_enable",
                    "name": name,
                    "status": "enabled",
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"Rule '{name}' enabled.")


@rules_app.command("disable")
def rules_disable_command(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Rule name to disable")],
) -> None:
    """Disable a rule in config.yaml."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_disable"):
        writer.disable_rule(target, name)

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "command": "rules_disable",
                    "name": name,
                    "status": "disabled",
                }
            )
        else:
            console = get_console(no_color=opts.no_color)
            console.print(f"Rule '{name}' disabled.")


@rules_app.command("test")
def rules_test_command(
    ctx: typer.Context,
    filename: Annotated[str, typer.Argument(help="Filename to evaluate against rules")],
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            help="Path to real file on disk to stat for size and mtime",
        ),
    ] = None,
    folder: Annotated[
        Path | None,
        typer.Option(
            "--folder",
            help="Watched root folder context for per-folder rule filtering",
        ),
    ] = None,
) -> None:
    """Explain every rule's match/non-match for a filename (docs/02 section 8)."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    target = opts.config if opts.config is not None else paths.config_path()

    with handle_errors(json_mode=opts.json_mode, command_name="rules_test"):
        cfg = loader.load(target)
        engine = build_engine(
            list(cfg.rules),
            cfg.settings,
            watch=list(cfg.watch_entries),
        )

        if file is not None:
            if not file.exists():
                sys.stderr.write(f"error: File not found: {file}\n")
                raise typer.Exit(code=2)
            info = FileInfo.from_path(file)
        else:
            p = Path(filename)
            info = FileInfo(
                path=p,
                name=p.name,
                stem=p.stem,
                ext=p.suffix[1:].lower() if p.suffix else "",
                size=0,
                mtime_ns=0,
                inode=0,
                device=0,
                is_regular=True,
                is_symlink=False,
            )

        raw_toks = raw_tokens(info.stem)
        camel_toks = camel_tokens(info.stem)
        traces = engine.trace(info)
        decision = engine.decide(info, folder=folder)

        # Map rules to destinations for trace output
        rule_map = {r.name.casefold(): r for r in cfg.rules}

        if opts.json_mode:
            traces_json = []
            for t in traces:
                r_spec = rule_map.get(t.rule_name.casefold())
                dest_str = safe_str(r_spec.destination) if r_spec else ""
                traces_json.append(
                    {
                        "rule": t.rule_name,
                        "priority": t.priority,
                        "matched": t.matched,
                        "reasons": list(t.reasons),
                        "destination": dest_str,
                    }
                )

            hit_rule = decision.hit.rule_name if decision.hit else None
            hit_dest = safe_str(decision.hit.dest_dir) if decision.hit else None
            hit_reason = (
                decision.hit.matched_by if decision.hit else (decision.reason or "no rule matched")
            )
            candidates = [c.rule_name for c in decision.candidates]

            print_json(
                {
                    "schema_version": 1,
                    "file": {
                        "name": info.name,
                        "path": safe_str(info.path),
                        "size": info.size,
                        "ext": info.ext,
                        "mtime_ns": info.mtime_ns,
                    },
                    "tokens": {
                        "raw": list(raw_toks),
                        "camel": list(camel_toks),
                    },
                    "traces": traces_json,
                    "decision": {
                        "kind": decision.kind.value,
                        "rule": hit_rule,
                        "destination": hit_dest,
                        "reason": hit_reason,
                        "candidates": candidates,
                    },
                }
            )
        else:
            hit_rule = decision.hit.rule_name if decision.hit else None
            hit_dest = safe_str(decision.hit.dest_dir) if decision.hit else None
            hit_reason = decision.hit.matched_by if decision.hit else decision.reason
            render_rule_trace(
                info.name,
                info.ext,
                info.size,
                raw_toks,
                camel_toks,
                traces,
                decision.kind,
                hit_rule,
                hit_dest,
                hit_reason,
                no_color=opts.no_color,
            )


@rules_app.command("check")
def rules_check_command(
    ctx: typer.Context,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Treat warnings as errors (exit code 3)",
        ),
    ] = False,
) -> None:
    """Check rules for overlaps, equal-priority shared keywords, and ambiguities."""
    from clutterxp.cli.cmd_config import config_validate_command

    config_validate_command(ctx, strict=strict)


__all__ = ["rules_app"]
