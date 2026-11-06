"""Background worker threads, QRunnable tasks, signals, and cancellation tokens.

Ensures the UI thread never performs blocking file I/O, DB access, or hashing.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

logger = logging.getLogger(__name__)

# Keep strong references to running workers to prevent premature GC by Python
_ACTIVE_WORKERS: set[TaskWorker] = set()


class WorkerSignals(QObject):
    """Signals emitted by background workers."""

    started = Signal()
    finished = Signal()
    error = Signal(Exception)
    result = Signal(object)
    progress = Signal(int, int)


class CancellationToken:
    """Thread-safe cancellation token wrapping a threading.Event."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._event.set()

    def reset(self) -> None:
        """Reset cancellation token."""
        self._event.clear()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def event(self) -> threading.Event:
        return self._event


class TaskWorker(QRunnable):
    """QRunnable wrapper to execute any blocking function on QThreadPool."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        cancel_event: threading.Event | CancellationToken | None = None,
        with_progress: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        if isinstance(cancel_event, CancellationToken):
            self.cancel_event: threading.Event | None = cancel_event.event
        else:
            self.cancel_event = cancel_event
        self.with_progress = with_progress
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def _progress_cb(self, current: int, total: int) -> None:
        self.signals.progress.emit(current, total)

    @Slot()
    def run(self) -> None:
        """Execute the task and emit result or error."""
        self.signals.started.emit()
        try:
            call_kwargs = dict(self.kwargs)
            if self.cancel_event is not None and "cancel" not in call_kwargs:
                call_kwargs["cancel"] = self.cancel_event
            if self.with_progress and "progress" not in call_kwargs:
                call_kwargs["progress"] = self._progress_cb

            res = self.fn(*self.args, **call_kwargs)
            try:
                self.signals.result.emit(res)
            except RuntimeError:
                return
        except Exception as exc:
            logger.exception("Task worker encountered exception: %s", exc)
            try:
                self.signals.error.emit(exc)
            except RuntimeError:
                return
        finally:
            with contextlib.suppress(RuntimeError):
                self.signals.finished.emit()


def run_in_worker(
    fn: Callable[..., Any],
    *args: Any,
    cancel_token: CancellationToken | threading.Event | None = None,
    with_progress: bool = False,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    pool: QThreadPool | None = None,
    **kwargs: Any,
) -> TaskWorker:
    """Helper to instantiate and launch a TaskWorker on the thread pool."""
    worker = TaskWorker(
        fn,
        *args,
        cancel_event=cancel_token,
        with_progress=with_progress,
        **kwargs,
    )

    _ACTIVE_WORKERS.add(worker)

    def _cleanup() -> None:
        _ACTIVE_WORKERS.discard(worker)
        if on_finished is not None:
            on_finished()

    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress)

    worker.signals.finished.connect(_cleanup)

    thread_pool = pool if pool is not None else QThreadPool.globalInstance()
    thread_pool.start(worker)
    return worker


__all__ = [
    "CancellationToken",
    "TaskWorker",
    "WorkerSignals",
    "run_in_worker",
]
