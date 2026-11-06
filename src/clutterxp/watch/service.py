"""Watch service: filesystem monitoring daemon, event debounce, settle, and move execution."""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver
from watchdog.observers.polling import PollingObserver

from ..config import loader
from ..config.runtime import RuntimeConfig
from ..config.schema import WatchEntry
from ..interfaces import Organizer
from ..locking import watch_lock
from ..models import (
    ClutterError,
    ErrorCode,
    FileInfo,
    PlanStatus,
    SafetyRefusal,
)
from ..paths import watch_lock_path
from .debounce import Debouncer
from .events import (
    SelfEventSuppression,
    WatcherEvent,
    WatchEventHandler,
    WatchEventKind,
)
from .ignore import BUILTIN_IGNORE, IgnoreMatcher
from .settle import Settler, SettleStatus

logger = logging.getLogger(__name__)


class WatchService:
    """Watch service managing observer threads, event queue, debouncer, settle checks,

    and executing moves via the Organizer.
    """

    def __init__(
        self,
        config_path: Path,
        organizer: Organizer,
        *,
        poll: bool = False,
        dry_run: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] | None = None,
        stat_fn: Callable[[Path | str], Any] | None = None,
        proc_dir: Path | None = None,
        watch_lock_file: Path | None = None,
        sample_interval: float | None = None,
    ) -> None:
        self.config_path = config_path
        self.organizer = organizer
        self.poll = poll
        self.dry_run = dry_run
        self.on_event = on_event

        self._clock = clock if clock is not None else time.monotonic
        self._stat_fn = stat_fn if stat_fn is not None else os.lstat
        self._proc_dir = proc_dir if proc_dir is not None else Path("/proc")
        lock_file = watch_lock_file if watch_lock_file is not None else watch_lock_path()
        self._watch_lock_file = lock_file

        self._debouncer = Debouncer(self._clock)
        settler_interval = sample_interval if sample_interval is not None else 0.5
        self._settler = Settler(
            self._clock,
            self._stat_fn,
            self._proc_dir,
            sample_interval=settler_interval,
        )
        self._suppression = SelfEventSuppression(self._clock)
        self._event_queue: queue.Queue[WatcherEvent] = queue.Queue()

        self._stop_event = threading.Event()
        self._reload_requested = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._lock_cm: Any = None

        self._observers: list[BaseObserver] = []
        self._watch_entries: dict[Path, WatchEntry] = {}
        self._ignore_matchers: dict[Path, IgnoreMatcher] = {}
        self._path_to_folder: dict[Path, Path] = {}
        self._ambiguous_reported: dict[Path, tuple[int, int]] = {}

        self._missing_folders: dict[Path, float] = {}
        self._retry_backoff: dict[Path, float] = {}

        self._config_mtime_ns: int = 0
        self._last_config_check: float = 0.0

        self.runtime_config: RuntimeConfig = loader.load(self.config_path)
        if not self.runtime_config.is_valid:
            raise SafetyRefusal(
                f"Invalid configuration in {config_path}: {'; '.join(self.runtime_config.errors)}",
                code=ErrorCode.DEST_INVALID,
            )
        self._config_mtime_ns = self.runtime_config.mtime_ns

    def _create_observer(self) -> BaseObserver:
        """Create a new observer instance (PollingObserver if poll=True else Observer)."""
        if self.poll:
            return PollingObserver()
        obs: BaseObserver = Observer()
        return obs

    def start(self) -> None:
        """Start watcher service (acquires lock, sets up observers, starts worker thread)."""
        # S14: Acquire single-instance watch lock
        try:
            self._lock_cm = watch_lock(self._watch_lock_file)
            self._lock_cm.__enter__()
        except ClutterError as e:
            raise SafetyRefusal(
                f"Another watcher is already running: {e}",
                code=ErrorCode.LOCK_TIMEOUT,
            ) from e

        self._stop_event.clear()
        self._reload_requested.clear()

        # Setup observers and entries
        self._setup_watchers()

        # Initial scan if configured
        self._run_initial_scans()

        # Start worker thread
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="clutterxp-watch-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self) -> None:
        """Gracefully stop watcher service.

        Stops observers, completes in-flight moves, and releases the watch lock.
        """
        self._stop_event.set()

        for obs in self._observers:
            with contextlib.suppress(Exception):
                obs.stop()

        for obs in self._observers:
            with contextlib.suppress(Exception):
                obs.join(timeout=2.0)
        self._observers.clear()

        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
            self._worker_thread = None

        if self._lock_cm is not None:
            with contextlib.suppress(Exception):
                self._lock_cm.__exit__(None, None, None)
            self._lock_cm = None

    def request_reload(self) -> None:
        """Signal service to reload configuration on next worker cycle."""
        self._reload_requested.set()

    def run(self) -> None:
        """Run service synchronously blocking until SIGINT / SIGTERM or stop()."""
        orig_sigint = None
        orig_sigterm = None
        orig_sighup = None

        def _sig_handler(signum: int, _frame: Any) -> None:
            if signum in (signal.SIGINT, signal.SIGTERM):
                self.stop()
            elif hasattr(signal, "SIGHUP") and signum == signal.SIGHUP:
                self.request_reload()

        if threading.current_thread() is threading.main_thread():
            orig_sigint = signal.signal(signal.SIGINT, _sig_handler)
            orig_sigterm = signal.signal(signal.SIGTERM, _sig_handler)
            if hasattr(signal, "SIGHUP"):
                orig_sighup = signal.signal(signal.SIGHUP, _sig_handler)

        try:
            self.start()
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=0.5)
        finally:
            self.stop()
            if threading.current_thread() is threading.main_thread():
                if orig_sigint is not None:
                    signal.signal(signal.SIGINT, orig_sigint)
                if orig_sigterm is not None:
                    signal.signal(signal.SIGTERM, orig_sigterm)
                if orig_sighup is not None and hasattr(signal, "SIGHUP"):
                    signal.signal(signal.SIGHUP, orig_sighup)

    def reload_config(self) -> bool:
        """Reload configuration live. Returns True on success, False on failure."""
        try:
            new_config = loader.load(self.config_path)
        except Exception as e:
            logger.error(
                "Failed to parse config from %s: %s; keeping existing config",
                self.config_path,
                e,
            )
            return False

        if not new_config.is_valid:
            logger.error(
                "Invalid config during reload (%s); keeping existing config",
                "; ".join(new_config.errors),
            )
            return False

        try:
            self.organizer.reload_config()
        except Exception as e:
            logger.error("Organizer reload_config failed: %s; keeping existing config", e)
            return False

        self.runtime_config = new_config
        self._config_mtime_ns = new_config.mtime_ns
        self._setup_watchers()
        logger.info("Configuration reloaded successfully from %s", self.config_path)
        return True

    def _setup_watchers(self) -> None:
        """Stop existing observers and configure observers for current watch entries."""
        for obs in self._observers:
            with contextlib.suppress(Exception):
                obs.stop()
                obs.join(timeout=1.0)
        self._observers.clear()
        self._watch_entries.clear()
        self._ignore_matchers.clear()

        for entry in self.runtime_config.watch_entries:
            folder = Path(entry.path)
            self._watch_entries[folder] = entry

            matcher = IgnoreMatcher(
                builtin=BUILTIN_IGNORE,
                global_extra=self.runtime_config.settings.ignore,
                folder_extra=entry.ignore,
                skip_hidden=self.runtime_config.settings.skip_hidden,
            )
            self._ignore_matchers[folder] = matcher

            if not folder.exists() or not folder.is_dir():
                now = self._clock()
                self._missing_folders[folder] = now + 30.0
                self._retry_backoff[folder] = 30.0
                logger.warning("Watched folder %s does not exist; will retry in 30s", folder)
                continue

            settle_sec = (
                entry.settle_seconds
                if entry.settle_seconds is not None
                else self.runtime_config.settings.settle_seconds
            )

            handler = WatchEventHandler(
                folder=folder,
                queue=self._event_queue,
                ignore_matcher=matcher,
                suppression=self._suppression,
                settle_seconds=settle_sec,
                is_candidate=lambda p: p in self._debouncer,
            )

            obs = self._create_observer()
            try:
                obs.schedule(handler, str(folder), recursive=entry.recursive)
                obs.start()
                self._observers.append(obs)
            except OSError as e:
                if getattr(e, "errno", None) == errno.ENOSPC:
                    raise ClutterError(
                        "inotify watch limit reached. Increase fs.inotify.max_user_watches: "
                        "sudo sysctl fs.inotify.max_user_watches=524288",
                        code=ErrorCode.PERMISSION,
                    ) from e
                logger.warning("Failed to watch folder %s: %s", folder, e)

    def _schedule_single_folder(self, folder: Path, entry: WatchEntry) -> None:
        """Schedule observer for a single recovered folder."""
        matcher = self._ignore_matchers[folder]
        settle_sec = (
            entry.settle_seconds
            if entry.settle_seconds is not None
            else self.runtime_config.settings.settle_seconds
        )
        handler = WatchEventHandler(
            folder=folder,
            queue=self._event_queue,
            ignore_matcher=matcher,
            suppression=self._suppression,
            settle_seconds=settle_sec,
            is_candidate=lambda p: p in self._debouncer,
        )
        obs = self._create_observer()
        try:
            obs.schedule(handler, str(folder), recursive=entry.recursive)
            obs.start()
            self._observers.append(obs)
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOSPC:
                raise ClutterError(
                    "inotify watch limit reached. Increase fs.inotify.max_user_watches: "
                    "sudo sysctl fs.inotify.max_user_watches=524288",
                    code=ErrorCode.PERMISSION,
                ) from e
            logger.warning("Failed to watch folder %s: %s", folder, e)

    def _run_initial_scans(self) -> None:
        """Run organizer.plan/apply at startup on folders with initial_scan: true."""
        for entry in self.runtime_config.watch_entries:
            if not entry.initial_scan:
                continue
            folder = Path(entry.path)
            if not folder.exists() or not folder.is_dir():
                continue

            rules_filter = entry.rules if isinstance(entry.rules, list) else None
            try:
                plan = self.organizer.plan(
                    [folder],
                    recursive=entry.recursive,
                    only_rules=rules_filter,
                )
                if self.dry_run:
                    for action in plan.actions:
                        if action.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED):
                            rule_name = action.decision.hit.rule_name if action.decision.hit else ""
                            self._emit_event(
                                event_type="dry_run",
                                src=action.file.path,
                                dst=action.dest_path,
                                rule=rule_name,
                            )
                else:
                    batch_res = self.organizer.apply(plan, command="watch")
                    for mr in batch_res.results:
                        if mr.ok and mr.dst:
                            self._suppression.suppress(mr.src, 5.0)
                            self._suppression.suppress(mr.dst, 5.0)
                            rule_name = mr.message or ""
                            self._emit_event(
                                event_type="moved",
                                src=mr.src,
                                dst=mr.dst,
                                rule=rule_name,
                            )
            except Exception as e:
                logger.error("Initial scan failed on %s: %s", folder, e)

    def _worker_loop(self) -> None:
        """Main worker thread loop: drain queue, settle due items, invoke organizer."""
        while not self._stop_event.is_set():
            # 1. Drain incoming event queue
            while True:
                try:
                    event = self._event_queue.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(event)

            # 2. Check debouncer overflow
            if self._debouncer.overflow:
                self._debouncer.overflow = False
                logger.warning("Debouncer queue overflowed; reconciling watched folders")
                for folder in list(self._watch_entries.keys()):
                    self._reconcile_folder(folder)

            # 3. Process due candidates
            now = self._clock()
            due_paths = self._debouncer.due(now)
            for path in due_paths:
                if self._stop_event.is_set():
                    break
                self._process_candidate(path)

            # 4. Check config mtime or SIGHUP reload
            if self._reload_requested.is_set() or (now - self._last_config_check >= 5.0):
                self._last_config_check = now
                self._reload_requested.clear()
                self._check_and_reload_config()

            # 5. Check missing folders retry
            self._check_missing_folders(now)

            # 6. Sleep / wait for next event or deadline
            next_dl = self._debouncer.next_deadline()
            now = self._clock()
            timeout = max(0.01, min(next_dl - now, 0.5)) if next_dl is not None else 0.5

            try:
                ev = self._event_queue.get(timeout=timeout)
                self._handle_event(ev)
            except queue.Empty:
                pass

    def _handle_event(self, event: WatcherEvent) -> None:
        """Handle normalized event from observer queue."""
        if event.kind == WatchEventKind.ARM:
            self._path_to_folder[event.path] = event.folder
            self._debouncer.arm(event.path, event.settle_seconds)
        elif event.kind == WatchEventKind.REARM:
            if event.path in self._debouncer:
                self._path_to_folder[event.path] = event.folder
                self._debouncer.arm(event.path, event.settle_seconds)
        elif event.kind == WatchEventKind.SHORTEN:
            if event.path in self._debouncer:
                current_dl = self._debouncer.get_deadline(event.path)
                shortened_delay = min(event.settle_seconds, 1.0)
                shortened_dl = self._clock() + shortened_delay
                if current_dl is None or shortened_dl < current_dl:
                    self._debouncer.arm(event.path, shortened_delay)
        elif event.kind == WatchEventKind.CANCEL:
            self._debouncer.cancel(event.path)
            self._settler.clear(event.path)
            self._path_to_folder.pop(event.path, None)
        elif event.kind == WatchEventKind.SCAN:
            self._reconcile_folder(event.folder)

    def _process_candidate(self, path: Path) -> None:
        """Run settle checks and process file if ready."""
        folder = self._find_folder_for_path(path)
        settle_sec = self._get_settle_seconds_for_folder(folder)

        status = self._settler.check(path, settle_sec)

        if status == SettleStatus.NOT_YET:
            # Re-arm with sample_interval delay to check stability
            delay = min(settle_sec, self._settler.sample_interval)
            self._debouncer.arm(path, delay)
            return

        if status in (SettleStatus.GONE, SettleStatus.DROP):
            self._debouncer.cancel(path)
            self._path_to_folder.pop(path, None)
            return

        # SettleStatus.READY:
        self._debouncer.cancel(path)
        self._path_to_folder.pop(path, None)

        if self.dry_run:
            try:
                plan = self.organizer.plan([path], recursive=False)
                for action in plan.actions:
                    if action.file.path == path:
                        if action.status in (PlanStatus.WILL_MOVE, PlanStatus.WILL_MOVE_RENAMED):
                            rule_name = action.decision.hit.rule_name if action.decision.hit else ""
                            self._emit_event(
                                event_type="dry_run",
                                src=path,
                                dst=action.dest_path,
                                rule=rule_name,
                            )
                        elif action.status == PlanStatus.AMBIGUOUS:
                            self._handle_ambiguous(action.file)
                        elif action.status == PlanStatus.BLOCKED:
                            self._emit_event(
                                event_type="blocked",
                                src=path,
                                error=action.message or action.code.value,
                            )
                        break
            except Exception as e:
                logger.error("Dry run plan failed on %s: %s", path, e)
            return

        # Normal run: invoke organizer.process_file
        try:
            batch_res = self.organizer.process_file(path, folder=folder, command="watch")
        except Exception as e:
            logger.error("process_file failed on %s: %s", path, e)
            self._emit_event(
                event_type="error",
                src=path,
                error=str(e),
            )
            return

        for mr in batch_res.results:
            if mr.ok and mr.dst:
                self._suppression.suppress(mr.src, 5.0)
                self._suppression.suppress(mr.dst, 5.0)
                rule_name = mr.message or ""
                self._emit_event(
                    event_type="moved",
                    src=mr.src,
                    dst=mr.dst,
                    rule=rule_name,
                )
            elif not mr.ok:
                self._emit_event(
                    event_type="blocked",
                    src=mr.src,
                    error=mr.message or mr.code.value,
                )

        if batch_res.moved == 0 and batch_res.skipped > 0:
            # File was skipped: check if ambiguous
            with contextlib.suppress(OSError, Exception):
                fi = FileInfo.from_path(path)
                plan = self.organizer.plan([path], recursive=False)
                if plan.actions and plan.actions[0].status == PlanStatus.AMBIGUOUS:
                    self._handle_ambiguous(fi)

    def _handle_ambiguous(self, file: FileInfo) -> None:
        """Report ambiguous file once de-duplicated until file changes."""
        key = (file.size, file.mtime_ns)
        if self._ambiguous_reported.get(file.path) == key:
            return
        self._ambiguous_reported[file.path] = key
        logger.warning("Ambiguous match for %s; file untouched", file.path)
        self._emit_event(
            event_type="ambiguous",
            src=file.path,
            error="ambiguous rules match",
        )

    def _find_folder_for_path(self, path: Path) -> Path | None:
        """Find the configured watched folder containing path."""
        folder = self._path_to_folder.get(path)
        if folder is not None:
            return folder
        for f in self._watch_entries:
            try:
                path.relative_to(f)
                return f
            except ValueError:
                pass
        return None

    def _get_settle_seconds_for_folder(self, folder: Path | None) -> float:
        """Get effective settle_seconds for folder."""
        if folder is not None and folder in self._watch_entries:
            entry = self._watch_entries[folder]
            if entry.settle_seconds is not None:
                return entry.settle_seconds
        return self.runtime_config.settings.settle_seconds

    def _reconcile_folder(self, folder: Path) -> None:
        """Trigger reconciliation scan for folder."""
        if not folder.exists() or not folder.is_dir():
            return
        entry = self._watch_entries.get(folder)
        recursive = entry.recursive if entry else False
        rules_filter = entry.rules if (entry and isinstance(entry.rules, list)) else None
        try:
            plan = self.organizer.plan([folder], recursive=recursive, only_rules=rules_filter)
            if not self.dry_run:
                batch_res = self.organizer.apply(plan, command="watch")
                for mr in batch_res.results:
                    if mr.ok and mr.dst:
                        self._suppression.suppress(mr.src, 5.0)
                        self._suppression.suppress(mr.dst, 5.0)
                        self._emit_event(
                            event_type="moved",
                            src=mr.src,
                            dst=mr.dst,
                            rule=mr.message or "",
                        )
        except Exception as e:
            logger.error("Reconciliation scan failed on %s: %s", folder, e)

    def _check_and_reload_config(self) -> None:
        """Check config file mtime and trigger reload if changed."""
        try:
            current_mtime = self.config_path.stat().st_mtime_ns
        except OSError:
            current_mtime = 0

        if current_mtime != self._config_mtime_ns:
            self.reload_config()

    def _check_missing_folders(self, now: float) -> None:
        """Check previously missing folders with exponential backoff up to 5 min."""
        for folder, next_retry in list(self._missing_folders.items()):
            if now < next_retry:
                continue
            if folder.exists() and folder.is_dir():
                logger.info("Watched folder %s has reappeared; starting watcher", folder)
                entry = self._watch_entries.get(folder)
                if entry is not None:
                    self._schedule_single_folder(folder, entry)
                del self._missing_folders[folder]
                self._retry_backoff.pop(folder, None)
            else:
                backoff = min(300.0, self._retry_backoff.get(folder, 30.0) * 1.5)
                self._retry_backoff[folder] = backoff
                self._missing_folders[folder] = now + backoff

    def _emit_event(
        self,
        event_type: str,
        src: Path,
        dst: Path | None = None,
        rule: str = "",
        error: str = "",
    ) -> None:
        """Format human-readable log and deliver structured event to on_event callback."""
        now = datetime.now(timezone.utc)
        time_str = now.strftime("%H:%M:%S")

        rule_display = f"  [{rule}]" if rule else ""
        if event_type == "moved":
            line = f"{time_str}  moved  {src.name}  ->  {dst}{rule_display}"
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
        elif event_type == "dry_run":
            line = f"{time_str}  [dry-run] would move {src.name}  ->  {dst}{rule_display}"
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
        elif event_type == "ambiguous":
            line = f"{time_str}  warning: ambiguous rules for {src.name}; file untouched"
            sys.stderr.write(f"{line}\n")
            sys.stderr.flush()
        elif event_type == "blocked":
            line = f"{time_str}  warning: blocked {src.name}: {error}"
            sys.stderr.write(f"{line}\n")
            sys.stderr.flush()

        if self.on_event is not None:
            payload: dict[str, Any] = {
                "schema_version": 1,
                "timestamp": now.isoformat(),
                "event": event_type,
                "src": str(src),
                "dst": str(dst) if dst is not None else None,
                "rule": rule if rule else None,
            }
            if error:
                payload["error"] = error
            try:
                self.on_event(payload)
            except Exception as e:
                logger.error("on_event callback raised error: %s", e)


__all__ = ["WatchService"]
