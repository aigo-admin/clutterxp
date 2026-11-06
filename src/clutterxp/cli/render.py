"""Console rendering with Rich, safe string escaping, and JSON output formatting."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from clutterxp.models import (
    BatchResult,
    DecisionKind,
    OperationRecord,
    Plan,
    PlanStatus,
    RuleTrace,
)


def safe_str(val: Any) -> str:
    """Format any object to string, safely escaping non-UTF-8 surrogate characters.

    Protects against UnicodeEncodeError when paths contain non-UTF-8 sequences.
    """
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="backslashreplace")
    s = str(val)
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", errors="surrogateescape").decode(
            "utf-8", errors="backslashreplace"
        )


def _sanitize_for_json(data: Any) -> Any:
    """Recursively convert Paths and strings with surrogates into clean JSON types."""
    if isinstance(data, Path):
        return safe_str(data)
    if isinstance(data, str):
        return safe_str(data)
    if isinstance(data, (int, float, bool)) or data is None:
        return data
    if isinstance(data, dict):
        return {_sanitize_for_json(k): _sanitize_for_json(v) for k, v in data.items()}
    if isinstance(data, (list, tuple, set)):
        return [_sanitize_for_json(item) for item in data]
    return safe_str(data)


def print_json(data: dict[str, Any]) -> None:
    """Emit ONLY pure valid JSON to stdout with no Rich formatting."""
    cleaned = _sanitize_for_json(data)
    text = json.dumps(cleaned, indent=2)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def get_console(
    *,
    no_color: bool = False,
    stderr: bool = False,
) -> Console:
    """Get a Rich Console instance configured for color settings."""
    env_no_color = bool(os.environ.get("NO_COLOR", "").strip())
    force_no_color = no_color or env_no_color
    return Console(
        file=sys.stderr if stderr else sys.stdout,
        no_color=force_no_color,
        highlight=False,
    )


@contextmanager
def progress_bar(
    description: str,
    total: int | None = None,
    *,
    quiet: bool = False,
    json_mode: bool = False,
    no_color: bool = False,
) -> Generator[Progress | None, None, None]:
    """Create a Rich progress bar rendered to stderr, active only when appropriate."""
    if quiet or json_mode or not sys.stderr.isatty():
        yield None
        return

    stderr_console = get_console(no_color=no_color, stderr=True)
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=stderr_console,
        transient=True,
    ) as progress:
        progress.add_task(description, total=total)
        yield progress


def render_plan(
    plan: Plan,
    *,
    dry_run: bool = False,
    show: str = "all",
    collapse_keep_limit: int = 10,
    no_color: bool = False,
) -> None:
    """Render human-readable execution plan with status words MOVE, KEEP, AMBIG, BLOCK."""
    console = get_console(no_color=no_color)

    # Header
    folder_str = ", ".join(safe_str(f) for f in plan.folders) if plan.folders else "targets"
    total_files = len(plan.actions)
    console.print(f"Plan for {folder_str}  ({total_files} files)\n")

    # Filter actions according to show
    will_move_count = 0
    unmatched_count = 0
    ambig_count = 0
    blocked_count = 0

    actions_to_show: list[tuple[str, str]] = []
    collapsed_keep = 0

    for action in plan.actions:
        src_name = safe_str(action.file.name)
        if action.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED):
            will_move_count += 1
            if show in ("all", "matched"):
                dst_dir = safe_str(action.dest_path.parent) if action.dest_path else "unknown_dest"
                rule_name = action.decision.hit.rule_name if action.decision.hit else ""
                matched_by = action.decision.hit.matched_by if action.decision.hit else ""
                info = f"[{rule_name}   {matched_by}]" if rule_name else ""
                actions_to_show.append(
                    ("MOVE", f" {src_name:<30} ->  {dst_dir:<32} {info}".strip())
                )
        elif action.status == PlanStatus.AMBIGUOUS:
            ambig_count += 1
            if show in ("all", "ambiguous"):
                candidates = (
                    ", ".join(c.rule_name for c in action.decision.candidates)
                    if action.decision.candidates
                    else "multiple rules"
                )
                actions_to_show.append(
                    ("AMBIG", f" {src_name:<30} candidates: {candidates}".strip())
                )
        elif action.status == PlanStatus.BLOCKED:
            blocked_count += 1
            if show in ("all", "blocked"):
                msg = action.message or action.code.value
                actions_to_show.append(("BLOCK", f" {src_name:<30} {msg}".strip()))
        else:  # NO_MATCH or IGNORED
            unmatched_count += 1
            if show in ("all", "unmatched"):
                reason = "left untouched"
                if action.status == PlanStatus.IGNORED:
                    reason = "ignored"
                elif action.decision.reason:
                    reason = f"{action.decision.reason}; left untouched"
                else:
                    reason = "no rule matched; left untouched"
                actions_to_show.append(("KEEP", f" {src_name:<30} {reason}".strip()))

    # Check if we should collapse KEEP lines in large plans when show == "all"
    for status_word, line_text in actions_to_show:
        if status_word == "KEEP" and show == "all" and unmatched_count > collapse_keep_limit:
            collapsed_keep += 1
            if collapsed_keep <= 3:
                _print_action_line(console, status_word, line_text, no_color=no_color)
            elif collapsed_keep == 4:
                rem = unmatched_count - 3
                console.print(f" ... ({rem} files left untouched, use --show unmatched to view)")
            # else skip printing remainder
        else:
            _print_action_line(console, status_word, line_text, no_color=no_color)

    # Summary
    console.print(
        f"\nSummary: {will_move_count} will move, {unmatched_count} unmatched (untouched), "
        f"{ambig_count} ambiguous, {blocked_count} blocked"
    )
    if dry_run:
        console.print("Nothing was changed (dry run).")


def _print_action_line(console: Console, status_word: str, text: str, no_color: bool) -> None:
    """Print an action line with color styling based on status word."""
    if no_color:
        console.print(f" {status_word:<6} {text}")
        return

    if status_word == "MOVE":
        console.print(f" [green bold]{status_word:<6}[/] {text}")
    elif status_word == "AMBIG":
        console.print(f" [yellow bold]{status_word:<6}[/] {text}")
    elif status_word == "BLOCK":
        console.print(f" [red bold]{status_word:<6}[/] {text}")
    else:  # KEEP
        console.print(f" [dim]{status_word:<6}[/] [dim]{text}[/]")


def render_organize_summary(
    plan: Plan,
    batch_result: BatchResult,
    *,
    no_color: bool = False,
) -> None:
    """Print summary after applying an organize plan."""
    console = get_console(no_color=no_color)
    unmatched = plan.count(PlanStatus.NO_MATCH) + plan.count(PlanStatus.IGNORED)
    ambiguous = plan.count(PlanStatus.AMBIGUOUS)

    console.print(
        f"\nSummary: {batch_result.moved} moved, {unmatched} unmatched (untouched), "
        f"{ambiguous} ambiguous, {batch_result.blocked} blocked, {batch_result.failed} failed"
    )
    if batch_result.batch_id is not None:
        console.print(f"Undo with: clutterxp undo --batch {batch_result.batch_id}")


def render_rules_table(
    rules: Sequence[Any],
    *,
    no_color: bool = False,
) -> None:
    """Render a table of rules (list command)."""
    console = get_console(no_color=no_color)
    table = Table(title="Configured Rules", header_style="bold")
    table.add_column("Name", style="bold")
    table.add_column("Priority", justify="right")
    table.add_column("Enabled", justify="center")
    table.add_column("Keywords")
    table.add_column("Extensions")
    table.add_column("Destination")

    for r in rules:
        keywords = ", ".join(r.keywords) if getattr(r, "keywords", None) else ""
        exts = ", ".join(r.extensions) if getattr(r, "extensions", None) else ""
        enabled_str = "yes" if getattr(r, "enabled", True) else "no"
        dest = safe_str(getattr(r, "destination", ""))
        table.add_row(
            safe_str(r.name),
            str(r.priority),
            enabled_str,
            keywords,
            exts,
            dest,
        )

    console.print(table)


def render_rule_trace(
    filename: str,
    ext: str,
    size: int,
    raw_toks: Sequence[str],
    camel_toks: Sequence[str],
    traces: Sequence[RuleTrace],
    decision_kind: DecisionKind,
    hit_rule: str | None,
    hit_dest: str | None,
    hit_reason: str,
    *,
    no_color: bool = False,
) -> None:
    """Render the explanation output for rules test command."""
    console = get_console(no_color=no_color)
    console.print(f"File: {filename}   (ext={ext}, {size} bytes)")
    console.print(f"tokens(raw)   : {' '.join(raw_toks)}")
    console.print(f"tokens(camel) : {' '.join(camel_toks)}\n")

    for t in traces:
        mark = "✓" if t.matched else "✗"
        reasons_text = (
            ", ".join(t.reasons) if t.reasons else ("matched" if t.matched else "no match")
        )
        rule_name = safe_str(t.rule_name)
        if no_color:
            console.print(f" {mark} {rule_name:<16} priority {t.priority:<4} {reasons_text}")
        elif t.matched:
            console.print(
                f" [green]{mark}[/] [bold]{rule_name:<16}[/] "
                f"priority {t.priority:<4} {reasons_text}"
            )
        else:
            console.print(
                f" [dim red]{mark}[/] [dim]{rule_name:<16}[/] "
                f"[dim]priority {t.priority:<4} {reasons_text}[/]"
            )

    console.print()
    if decision_kind == DecisionKind.MATCH:
        dest_text = f" ->  {hit_dest}" if hit_dest else ""
        match_line = f"Result: MATCH  {hit_rule}{dest_text}"
        console.print(f"[green bold]{match_line}[/]" if not no_color else match_line)
    elif decision_kind == DecisionKind.AMBIGUOUS:
        ambig_line = f"Result: AMBIGUOUS  {hit_reason}"
        console.print(f"[yellow bold]{ambig_line}[/]" if not no_color else ambig_line)
    else:
        nomatch_line = f"Result: NO_MATCH  {hit_reason or 'no rule matched'}"
        console.print(f"[dim]{nomatch_line}[/]" if not no_color else nomatch_line)


def render_history_table(
    records: Sequence[OperationRecord],
    *,
    no_color: bool = False,
) -> None:
    """Render table of history operations."""
    console = get_console(no_color=no_color)
    table = Table(title="History Operations", header_style="bold")
    table.add_column("ID", justify="right")
    table.add_column("Batch", justify="right")
    table.add_column("Status")
    table.add_column("Rule")
    table.add_column("Source")
    table.add_column("Destination")
    table.add_column("Created At")

    for rec in records:
        status_str = rec.status.value
        table.add_row(
            str(rec.id),
            str(rec.batch_id),
            status_str,
            safe_str(rec.rule_name or ""),
            safe_str(rec.src_path),
            safe_str(rec.dst_path or ""),
            safe_str(rec.created_at[:19]),
        )

    console.print(table)


def render_doctor_results(
    checks: list[dict[str, Any]],
    fixes: list[dict[str, Any]],
    *,
    no_color: bool = False,
) -> None:
    """Render doctor checklist and fixes applied."""
    console = get_console(no_color=no_color)
    console.print("[bold]ClutterXP Doctor Diagnostics[/bold]\n")

    for c in checks:
        status = c.get("status", "ok")
        name = c.get("name", "")
        message = c.get("message", "")
        if status == "ok":
            mark = "[green]✓[/green]" if not no_color else "✓"
            console.print(f" {mark} {name}: {message}")
        elif status == "warning":
            mark = "[yellow]![/yellow]" if not no_color else "!"
            console.print(f" {mark} {name}: {message}")
        else:  # error
            mark = "[red]✗[/red]" if not no_color else "✗"
            console.print(f" {mark} {name}: {message}")

    if fixes:
        console.print("\n[bold]Fixes applied:[/bold]")
        for f in fixes:
            console.print(f" - {f.get('check')}: {f.get('message')}")


__all__ = [
    "get_console",
    "print_json",
    "progress_bar",
    "render_doctor_results",
    "render_history_table",
    "render_organize_summary",
    "render_plan",
    "render_rule_trace",
    "render_rules_table",
    "safe_str",
]
