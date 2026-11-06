"""Command handler for `clutterxp doctor` diagnostics and recovery."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from clutterxp import paths
from clutterxp.cli.errors import handle_errors
from clutterxp.cli.render import print_json, render_doctor_results, safe_str
from clutterxp.config import loader
from clutterxp.history.db import integrity_check
from clutterxp.history.recovery import reconcile
from clutterxp.history.service import HistoryService

if TYPE_CHECKING:
    from clutterxp.cli.app import GlobalOptions


def doctor_command(
    ctx: typer.Context,
    fix: Annotated[
        bool,
        typer.Option(
            "--fix",
            help="Automatically reconcile pending ops, clean partial files, and fix permissions",
        ),
    ] = False,
) -> None:
    """Run diagnostics on configuration, database integrity, locks, and filesystem health."""
    from clutterxp.cli.app import get_globals

    opts: GlobalOptions = get_globals(ctx)
    checks: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []

    with handle_errors(json_mode=opts.json_mode, command_name="doctor"):
        # 1. Root check
        if hasattr(os, "geteuid") and os.geteuid() == 0 and not opts.allow_root:
            checks.append(
                {
                    "name": "running_as_root",
                    "status": "error",
                    "message": (
                        "Running as root is forbidden for safety. Pass --allow-root to override."
                    ),
                }
            )
        else:
            checks.append(
                {
                    "name": "running_as_root",
                    "status": "ok",
                    "message": "Running as standard unprivileged user.",
                }
            )

        # 2. Config validity
        cfg_path = opts.config if opts.config is not None else paths.config_path()
        cfg_valid = False
        loaded_cfg = None
        if not cfg_path.exists():
            checks.append(
                {
                    "name": "config_validity",
                    "status": "error",
                    "message": f"Config file not found at {safe_str(cfg_path)}",
                }
            )
        else:
            try:
                loaded_cfg = loader.load(cfg_path)
                if loaded_cfg.is_valid:
                    cfg_valid = True
                    checks.append(
                        {
                            "name": "config_validity",
                            "status": "ok",
                            "message": f"Config valid ({len(loaded_cfg.rules)} active rules)",
                        }
                    )
                else:
                    checks.append(
                        {
                            "name": "config_validity",
                            "status": "error",
                            "message": f"Config errors: {', '.join(loaded_cfg.errors)}",
                        }
                    )
            except Exception as e:
                checks.append(
                    {
                        "name": "config_validity",
                        "status": "error",
                        "message": f"Failed to load config: {e}",
                    }
                )

        # 3. Database integrity
        db_path = paths.history_db_path()
        if not db_path.exists():
            checks.append(
                {
                    "name": "database_integrity",
                    "status": "ok",
                    "message": "History database does not exist yet (clean state).",
                }
            )
        else:
            try:
                conn = sqlite3.connect(db_path)
                is_ok = integrity_check(conn)
                if is_ok:
                    with contextlib.suppress(sqlite3.Error):
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    checks.append(
                        {
                            "name": "database_integrity",
                            "status": "ok",
                            "message": "Database passed PRAGMA integrity_check.",
                        }
                    )
                else:
                    checks.append(
                        {
                            "name": "database_integrity",
                            "status": "error",
                            "message": "Database failed PRAGMA integrity_check.",
                        }
                    )
                conn.close()
            except Exception as e:
                checks.append(
                    {
                        "name": "database_integrity",
                        "status": "error",
                        "message": f"Database check exception: {e}",
                    }
                )

        # 4. Pending operations & reconciliation
        if db_path.exists():
            try:
                history = HistoryService(db_path)
                pending = history.pending_ops()
                if pending:
                    if fix:
                        actions = reconcile(history)
                        fixes.append(
                            {
                                "check": "pending_operations",
                                "action": "reconciled",
                                "message": f"Reconciled {len(actions)} pending operations.",
                            }
                        )
                        checks.append(
                            {
                                "name": "pending_operations",
                                "status": "ok",
                                "message": f"Reconciled {len(pending)} pending operations.",
                            }
                        )
                    else:
                        checks.append(
                            {
                                "name": "pending_operations",
                                "status": "error",
                                "message": (
                                    f"Found {len(pending)} un-reconciled pending operations. "
                                    "Run with --fix to recover."
                                ),
                            }
                        )
                else:
                    checks.append(
                        {
                            "name": "pending_operations",
                            "status": "ok",
                            "message": "No pending operations in history journal.",
                        }
                    )
            except Exception as e:
                checks.append(
                    {
                        "name": "pending_operations",
                        "status": "error",
                        "message": f"Failed to check pending operations: {e}",
                    }
                )
        else:
            checks.append(
                {
                    "name": "pending_operations",
                    "status": "ok",
                    "message": "No history database present.",
                }
            )

        # 5. Stale partial files (*.clutterxp-partial older than 1h)
        dirs_to_check: set[Path] = set()
        if loaded_cfg:
            for w in loaded_cfg.watch_entries:
                with contextlib.suppress(Exception):
                    dirs_to_check.add(paths.expand(w.path))
            for r in loaded_cfg.all_rules:
                with contextlib.suppress(Exception):
                    expanded_dest = paths.expand(r.destination)
                    dirs_to_check.add(expanded_dest)

        stale_files: list[Path] = []
        now_ts = time.time()
        for d in dirs_to_check:
            if d.exists() and d.is_dir():
                for p in d.glob("**/*.clutterxp-partial"):
                    try:
                        if now_ts - p.stat().st_mtime > 3600:
                            stale_files.append(p)
                    except OSError:
                        pass

        if stale_files:
            if fix:
                removed_count = 0
                for sf in stale_files:
                    try:
                        sf.unlink()
                        removed_count += 1
                    except OSError:
                        pass
                fixes.append(
                    {
                        "check": "stale_partial_files",
                        "action": "cleaned",
                        "message": (
                            f"Removed {removed_count} stale partial files older than 1 hour."
                        ),
                    }
                )
                checks.append(
                    {
                        "name": "stale_partial_files",
                        "status": "ok",
                        "message": f"Cleaned {removed_count} stale partial files.",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "stale_partial_files",
                        "status": "error",
                        "message": (
                            f"Found {len(stale_files)} stale partial files older than 1 hour. "
                            "Run with --fix to clean."
                        ),
                    }
                )
        else:
            checks.append(
                {
                    "name": "stale_partial_files",
                    "status": "ok",
                    "message": "No stale partial files found.",
                }
            )

        # 6. Destination reachability and writability
        if loaded_cfg and cfg_valid:
            unwritable: list[str] = []
            for r in loaded_cfg.rules:
                try:
                    exp_dest = paths.expand(r.destination)
                    curr: Path | None = exp_dest
                    while curr and not curr.exists() and curr != curr.parent:
                        curr = curr.parent
                    if curr and curr.exists():
                        if not os.access(curr, os.W_OK):
                            unwritable.append(f"{r.name} -> {curr} not writable")
                    else:
                        unwritable.append(f"{r.name} -> {exp_dest} unreachable")
                except Exception as e:
                    unwritable.append(f"{r.name} -> {e}")

            if unwritable:
                checks.append(
                    {
                        "name": "destination_permissions",
                        "status": "error",
                        "message": f"Destination access issues: {'; '.join(unwritable)}",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "destination_permissions",
                        "status": "ok",
                        "message": "All rule destinations are reachable and writable.",
                    }
                )

        # 7. Filesystem boundaries
        if loaded_cfg and cfg_valid and loaded_cfg.watch_entries and loaded_cfg.rules:
            cross_dev = False
            for w in loaded_cfg.watch_entries:
                with contextlib.suppress(Exception):
                    w_path = paths.expand(w.path)
                    if w_path.exists():
                        w_dev = w_path.stat().st_dev
                        for r in loaded_cfg.rules:
                            r_dest = paths.expand(r.destination)
                            curr = r_dest
                            while curr and not curr.exists() and curr != curr.parent:
                                curr = curr.parent
                            if curr and curr.exists() and curr.stat().st_dev != w_dev:
                                cross_dev = True
                                break
            checks.append(
                {
                    "name": "filesystem_boundaries",
                    "status": "ok",
                    "message": (
                        "Cross-filesystem boundaries detected (will use safe copy-verify)."
                        if cross_dev
                        else "All paths share the same filesystem (fast rename available)."
                    ),
                }
            )

        # 8. Inotify limits
        inotify_path = Path("/proc/sys/fs/inotify/max_user_watches")
        if inotify_path.exists():
            try:
                val = int(inotify_path.read_text().strip())
                if val < 8192:
                    checks.append(
                        {
                            "name": "inotify_limits",
                            "status": "warning",
                            "message": f"max_user_watches is low ({val}). Recommended >= 524288.",
                        }
                    )
                else:
                    checks.append(
                        {
                            "name": "inotify_limits",
                            "status": "ok",
                            "message": f"max_user_watches is adequate ({val}).",
                        }
                    )
            except Exception:
                checks.append(
                    {
                        "name": "inotify_limits",
                        "status": "ok",
                        "message": "Could not parse inotify max_user_watches.",
                    }
                )

        # 9. Lock files check
        fs_lock = paths.fs_lock_path()
        watch_lock = paths.watch_lock_path()
        checks.append(
            {
                "name": "lock_files",
                "status": "ok",
                "message": f"Lock paths: {safe_str(fs_lock)}, {safe_str(watch_lock)}",
            }
        )

        # 10. File permissions on state directory and log files
        perm_issues = False
        state_d = paths.state_dir()
        if state_d.exists():
            mode = state_d.stat().st_mode & 0o777
            if mode != 0o700:
                if fix:
                    state_d.chmod(0o700)
                    fixes.append(
                        {
                            "check": "state_permissions",
                            "action": "chmod 0700",
                            "message": f"Corrected permissions on {safe_str(state_d)}",
                        }
                    )
                else:
                    perm_issues = True

        log_p = paths.log_path()
        if log_p.exists():
            mode = log_p.stat().st_mode & 0o777
            if mode != 0o600:
                if fix:
                    log_p.chmod(0o600)
                    fixes.append(
                        {
                            "check": "log_permissions",
                            "action": "chmod 0600",
                            "message": f"Corrected permissions on {safe_str(log_p)}",
                        }
                    )
                else:
                    perm_issues = True

        if perm_issues:
            checks.append(
                {
                    "name": "state_permissions",
                    "status": "error",
                    "message": (
                        "State files or directory have insecure permissions (expected 0700/0600). "
                        "Run with --fix."
                    ),
                }
            )
        else:
            checks.append(
                {
                    "name": "state_permissions",
                    "status": "ok",
                    "message": "State directory and files have secure permissions (0700/0600).",
                }
            )

        # Overall diagnosis
        has_error = any(c["status"] == "error" for c in checks)
        all_ok = not has_error

        if opts.json_mode:
            print_json(
                {
                    "schema_version": 1,
                    "ok": all_ok,
                    "checks": checks,
                    "fixes": fixes,
                }
            )
        else:
            render_doctor_results(checks, fixes, no_color=opts.no_color)

        if has_error:
            raise typer.Exit(code=3)


__all__ = ["doctor_command"]
