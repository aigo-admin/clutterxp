"""GUI API Adapter for ClutterXP.

Exposes a clean facade over Organizer, RuleEngine, Mover, History, Config,
Watcher, and Duplicates modules. Views interact solely through this adapter.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import shutil
import subprocess
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clutterxp import paths, service_install
from clutterxp.config import loader, validate, writer
from clutterxp.config.runtime import RuntimeConfig
from clutterxp.config.schema import Config, RuleSpec, WatchEntry
from clutterxp.config.writer import _load_yaml, _save_atomic
from clutterxp.duplicates.detector import DuplicateDetectorImpl
from clutterxp.duplicates.hashcache import HashCache
from clutterxp.duplicates.quarantine import (
    QuarantinePlan,
    apply_quarantine,
    plan_quarantine,
)
from clutterxp.history.service import HistoryService
from clutterxp.history.undo import UndoService
from clutterxp.interfaces import (
    DuplicateDetector,
    History,
    Mover,
    Organizer,
    RuleEngine,
)
from clutterxp.models import (
    BatchResult,
    ConfigError,
    DuplicateGroup,
    FileInfo,
    OperationRecord,
    OpStatus,
    Plan,
    ProgressCb,
    RuleTrace,
    UndoOutcome,
)
from clutterxp.mover.safe_move import SafeMover
from clutterxp.organizer import OrganizerImpl
from clutterxp.rules import build_engine
from clutterxp.watch.service import WatchService

logger = logging.getLogger(__name__)


def add_watch_folder(config_path: Path, entry: dict[str, Any] | WatchEntry) -> None:
    """Add a watch folder to configuration preserving comments."""
    yaml, doc = _load_yaml(config_path)
    entry_dict = (
        entry.model_dump(exclude_unset=True) if isinstance(entry, WatchEntry) else dict(entry)
    )

    path_str = str(entry_dict.get("path", "")).strip()
    if not path_str:
        raise ConfigError("Watch folder must have a non-empty path")

    watch_list = doc.setdefault("watch", [])
    normalized = path_str.rstrip("/")
    for w in watch_list:
        if isinstance(w, dict) and str(w.get("path", "")).strip().rstrip("/") == normalized:
            raise ConfigError(f"Watch entry for '{path_str}' already exists")

    watch_list.append(entry_dict)
    _save_atomic(config_path, doc, yaml)


def remove_watch_folder(config_path: Path, watch_path: str) -> None:
    """Remove a watch folder from configuration preserving comments."""
    yaml, doc = _load_yaml(config_path)
    watch_list = doc.get("watch", [])
    target_idx = -1
    normalized = watch_path.strip().rstrip("/")
    for i, w in enumerate(watch_list):
        if isinstance(w, dict) and str(w.get("path", "")).strip().rstrip("/") == normalized:
            target_idx = i
            break

    if target_idx == -1:
        raise ConfigError(f"Watch folder '{watch_path}' not found in configuration")

    del watch_list[target_idx]
    _save_atomic(config_path, doc, yaml)


def edit_watch_folder(config_path: Path, watch_path: str, updates: dict[str, Any]) -> None:
    """Edit watch folder properties in configuration preserving comments."""
    yaml, doc = _load_yaml(config_path)
    watch_list = doc.get("watch", [])
    target: dict[str, Any] | None = None
    normalized = watch_path.strip().rstrip("/")
    for w in watch_list:
        if isinstance(w, dict) and str(w.get("path", "")).strip().rstrip("/") == normalized:
            target = w
            break

    if target is None:
        raise ConfigError(f"Watch folder '{watch_path}' not found in configuration")

    target.update(updates)
    _save_atomic(config_path, doc, yaml)


class GuiApi:
    """Adapter exposing clean high-level operations for GUI views."""

    def __init__(
        self,
        config_path: Path | None = None,
        organizer: Organizer | None = None,
        history: History | None = None,
        mover: Mover | None = None,
        detector: DuplicateDetector | None = None,
    ) -> None:
        self.config_path: Path = config_path if config_path is not None else paths.config_path()
        self._history: History | None = history
        self._mover: Mover | None = mover
        self._organizer: Organizer | None = organizer
        self._detector: DuplicateDetector | None = detector
        self._in_app_watcher: WatchService | None = None
        self._cached_runtime_config: RuntimeConfig | None = None

    @property
    def history(self) -> History:
        if self._history is None:
            self._history = HistoryService(paths.history_db_path())
        return self._history

    @property
    def mover(self) -> Mover:
        if self._mover is None:
            cfg = self.get_runtime_config()
            self._mover = SafeMover(verify_copy=cfg.settings.verify_copy)
        return self._mover

    @property
    def organizer(self) -> Organizer:
        if self._organizer is None:
            cfg = self.get_runtime_config()
            engine = build_engine(
                list(cfg.rules),
                cfg.settings,
                watch=list(cfg.watch_entries),
            )
            undo_svc = UndoService(self.history, self.mover, paths.fs_lock_path())
            self._organizer = OrganizerImpl(
                cfg,
                engine,
                self.mover,
                self.history,
                undo_service=undo_svc,
            )
        return self._organizer

    @property
    def engine(self) -> RuleEngine:
        if isinstance(self.organizer, OrganizerImpl):
            return self.organizer.engine
        cfg = self.get_runtime_config()
        return build_engine(
            list(cfg.rules),
            cfg.settings,
            watch=list(cfg.watch_entries),
        )

    # ----------------------------------------------------------------------- Config
    def get_runtime_config(self, force_reload: bool = False) -> RuntimeConfig:
        """Load and return current runtime configuration."""
        if not self.config_path.exists():
            writer.init_config(self.config_path, minimal=True)
        if self._cached_runtime_config is None or force_reload:
            self._cached_runtime_config = loader.load(self.config_path)
        return self._cached_runtime_config

    def reload_config(self) -> RuntimeConfig:
        """Force-reload configuration and update organizer."""
        cfg = self.get_runtime_config(force_reload=True)
        if isinstance(self.organizer, OrganizerImpl):
            self.organizer.reload_config()
        return cfg

    def config_validate(self) -> tuple[list[str], list[str]]:
        """Validate configuration, returning (errors, warnings)."""
        cfg = self.get_runtime_config(force_reload=True)
        errors = list(cfg.errors)
        warnings = list(cfg.warnings)
        try:
            _, doc = _load_yaml(self.config_path)
            model = Config.model_validate(doc)
            val_errs, val_warns = validate(model)
            for e in val_errs:
                if e not in errors:
                    errors.append(e)
            for w in val_warns:
                if w not in warnings:
                    warnings.append(w)
        except Exception as exc:
            msg = str(exc)
            if msg not in errors:
                errors.append(msg)
        return errors, warnings

    def config_save_rule(self, rule: dict[str, Any] | RuleSpec, is_new: bool = True) -> None:
        """Add or update a rule and reload config."""
        if is_new:
            writer.add_rule(self.config_path, rule)
        else:
            rule_name = rule.name if isinstance(rule, RuleSpec) else str(rule["name"])
            updates = (
                rule.model_dump(exclude_unset=True) if isinstance(rule, RuleSpec) else dict(rule)
            )
            writer.edit_rule(self.config_path, rule_name, updates)
        self.reload_config()

    def config_remove_rule(self, rule_name: str) -> None:
        """Remove a rule and reload config."""
        writer.remove_rule(self.config_path, rule_name)
        self.reload_config()

    def config_enable_rule(self, rule_name: str) -> None:
        """Enable a rule and reload config."""
        writer.enable_rule(self.config_path, rule_name)
        self.reload_config()

    def config_disable_rule(self, rule_name: str) -> None:
        """Disable a rule and reload config."""
        writer.disable_rule(self.config_path, rule_name)
        self.reload_config()

    def config_add_watch(self, entry: dict[str, Any] | WatchEntry) -> None:
        """Add a watch folder and reload config."""
        add_watch_folder(self.config_path, entry)
        self.reload_config()

    def config_remove_watch(self, watch_path: str) -> None:
        """Remove a watch folder and reload config."""
        remove_watch_folder(self.config_path, watch_path)
        self.reload_config()

    def config_edit_watch(self, watch_path: str, updates: dict[str, Any]) -> None:
        """Edit watch folder properties and reload config."""
        edit_watch_folder(self.config_path, watch_path, updates)
        self.reload_config()

    # ----------------------------------------------------------------------- Rules
    def rules_trace(self, file: FileInfo | Path | str) -> list[RuleTrace]:
        """Explain matching logic for a file or filename."""
        if isinstance(file, FileInfo):
            info = file
        else:
            p = Path(file)
            if p.exists() and p.is_file():
                info = FileInfo.from_path(p)
            else:
                info = FileInfo(
                    path=p,
                    name=p.name,
                    stem=p.stem,
                    ext=p.suffix[1:].lower() if p.suffix else "",
                    size=1024,
                    mtime_ns=int(time.time() * 1_000_000_000),
                    inode=1,
                    device=1,
                    is_regular=True,
                    is_symlink=False,
                )
        return list(self.engine.trace(info))

    # ----------------------------------------------------------------------- Plan & Apply
    def plan(
        self,
        paths_to_scan: Iterable[Path] | None = None,
        *,
        recursive: bool | None = None,
        only_rules: Sequence[str] | None = None,
    ) -> Plan:
        """Generate a dry-run execution plan."""
        return self.organizer.plan(
            paths=paths_to_scan,
            recursive=recursive,
            only_rules=only_rules,
        )

    def apply(self, plan: Plan, *, progress: ProgressCb | None = None) -> BatchResult:
        """Execute the planned file operations."""
        return self.organizer.apply(plan, command="organize", progress=progress)

    # ----------------------------------------------------------------------- History & Undo
    def history_list(
        self,
        *,
        batch_id: int | None = None,
        status: OpStatus | None = None,
        limit: int = 50,
        since_iso: str | None = None,
        rule_name: str | None = None,
        path_contains: str | None = None,
    ) -> Sequence[OperationRecord]:
        """Query journal operations."""
        return self.history.list_ops(
            batch_id=batch_id,
            status=status,
            limit=limit,
            since_iso=since_iso,
            rule_name=rule_name,
            path_contains=path_contains,
        )

    def history_get_op(self, op_id: int) -> OperationRecord | None:
        """Retrieve single operation by id."""
        return self.history.get_op(op_id)

    def undo(
        self,
        *,
        op_id: int | None = None,
        batch_id: int | None = None,
        dry_run: bool = False,
        on_conflict: str = "skip",
        allow_modified: bool = False,
    ) -> Sequence[UndoOutcome]:
        """Undo an operation or batch."""
        return self.organizer.undo(
            op_id=op_id,
            batch_id=batch_id,
            dry_run=dry_run,
            on_conflict=on_conflict,
            allow_modified=allow_modified,
        )

    def undo_last_batch(self, *, dry_run: bool = False) -> Sequence[UndoOutcome]:
        """Undo the most recent successful batch."""
        last_id = self.history.last_batch_id()
        if last_id is None:
            return []
        return self.undo(batch_id=last_id, dry_run=dry_run)

    def get_today_move_count(self) -> int:
        """Count operations successfully moved today (UTC)."""
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "T00:00:00Z"
        ops = self.history.list_ops(status=OpStatus.DONE, limit=10_000, since_iso=today_iso)
        return len(ops)

    # ------------------------------------------------------------------ Duplicates & Quarantine
    def duplicates_scan(
        self,
        folders: Sequence[Path],
        *,
        recursive: bool = True,
        min_size: int = 1,
        exclude: Sequence[str] = (),
        jobs: int = 1,
        potential: bool = True,
        cancel: Any = None,
        progress: ProgressCb | None = None,
    ) -> Sequence[DuplicateGroup]:
        """Scan folders for duplicate files."""
        if self._detector is not None:
            return self._detector.scan(
                folders,
                recursive=recursive,
                min_size=min_size,
                exclude=exclude,
                jobs=jobs,
                potential=potential,
                progress=progress,
            )

        cache = HashCache(paths.cache_db_path())
        detector = DuplicateDetectorImpl(cache=cache, cancel=cancel)
        return detector.scan(
            folders,
            recursive=recursive,
            min_size=min_size,
            exclude=exclude,
            jobs=jobs,
            potential=potential,
            progress=progress,
        )

    def duplicates_plan_quarantine(
        self,
        groups: Sequence[DuplicateGroup],
        quarantine_dir: Path | None = None,
        *,
        keep_strategy: str = "oldest",
        keep_path: Path | None = None,
    ) -> QuarantinePlan:
        """Build quarantine plan for duplicate groups."""
        target_dir = (
            quarantine_dir if quarantine_dir is not None else (paths.state_dir() / "quarantine")
        )
        return plan_quarantine(
            groups,
            target_dir,
            keep_strategy=keep_strategy,
            keep_path=keep_path,
        )

    def duplicates_quarantine(self, plan: QuarantinePlan) -> BatchResult:
        """Execute quarantine plan."""
        return apply_quarantine(plan, self.mover, self.history)

    # ----------------------------------------------------------------------- Watcher Management
    def watcher_status(self) -> str:
        """Return 'running' or 'stopped' for the watcher service."""
        if (
            self._in_app_watcher is not None
            and self._in_app_watcher._worker_thread is not None
            and self._in_app_watcher._worker_thread.is_alive()
        ):
            return "running"

        # Check if systemd user unit is installed and running
        systemctl = shutil.which("systemctl")
        if service_install.get_unit_path().exists() and systemctl:
            with contextlib.suppress(Exception):
                proc = subprocess.run(  # noqa: S603
                    [systemctl, "--user", "is-active", "clutterxp.service"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if proc.stdout.strip() == "active":
                    return "running"

        # Check if watch lock is held by another process
        lock_p = paths.watch_lock_path()
        if lock_p.exists():
            with contextlib.suppress(Exception), lock_p.open("r+") as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (BlockingIOError, OSError):
                    return "running"

        return "stopped"

    def watcher_start(self) -> None:
        """Start watcher via systemd service if available, else in-app thread."""
        if self.watcher_status() == "running":
            return

        systemctl = shutil.which("systemctl")
        if service_install.get_unit_path().exists() and systemctl:
            with contextlib.suppress(subprocess.CalledProcessError):
                subprocess.run(  # noqa: S603
                    [systemctl, "--user", "start", "clutterxp.service"],
                    check=True,
                    capture_output=True,
                )
                return

        # In-app background watcher
        if self._in_app_watcher is None:
            self._in_app_watcher = WatchService(self.config_path, self.organizer)
        self._in_app_watcher.start()

    def watcher_stop(self) -> None:
        """Stop watcher via systemd service or in-app thread."""
        systemctl = shutil.which("systemctl")
        if service_install.get_unit_path().exists() and systemctl:
            with contextlib.suppress(Exception):
                subprocess.run(  # noqa: S603
                    [systemctl, "--user", "stop", "clutterxp.service"],
                    check=False,
                    capture_output=True,
                )

        if self._in_app_watcher is not None:
            self._in_app_watcher.stop()
            self._in_app_watcher = None


__all__ = [
    "GuiApi",
    "add_watch_folder",
    "edit_watch_folder",
    "remove_watch_folder",
]
