"""Test A89: UI thread never blocked during scans/moves.

Cancellation token stops cleanly within 1s.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QCoreApplication
from pytestqt.qtbot import QtBot

from clutterxp.gui.workers import CancellationToken, run_in_worker


def test_A89_ui_thread_never_blocks_and_cancels_cleanly_within_1s(qtbot: QtBot) -> None:
    # 1. Start a slow/blocking task in background worker
    token = CancellationToken()
    worker_started = threading.Event()
    worker_finished = threading.Event()
    events_processed = 0

    def slow_task(*, cancel: threading.Event) -> str:
        worker_started.set()
        # Simulate long scanning/moving operation with cooperative cancellation check
        for _ in range(500):
            if cancel.is_set():
                worker_finished.set()
                return "cancelled"
            time.sleep(0.01)
        worker_finished.set()
        return "completed"

    finished_called = False
    result_received: str | None = None

    def on_result(res: str) -> None:
        nonlocal result_received
        result_received = res

    def on_finished() -> None:
        nonlocal finished_called
        finished_called = True

    worker = run_in_worker(
        slow_task,
        cancel_token=token,
        on_result=on_result,
        on_finished=on_finished,
    )
    assert worker is not None

    # Wait for worker thread to actually start
    qtbot.waitUntil(lambda: worker_started.is_set(), timeout=2000)

    # 2. Verify UI thread is responsive and not blocked by background worker
    start_time = time.monotonic()
    while time.monotonic() - start_time < 0.2:
        QCoreApplication.processEvents()
        events_processed += 1
        time.sleep(0.01)

    assert events_processed > 5, "UI thread was blocked and unable to process Qt events"

    # 3. Cancel worker and verify it halts cleanly within 1.0 second
    cancel_time = time.monotonic()
    token.cancel()
    assert token.is_cancelled

    qtbot.waitUntil(lambda: worker_finished.is_set(), timeout=1000)
    stop_duration = time.monotonic() - cancel_time
    assert stop_duration < 1.0, (
        f"Worker took too long to cancel: {stop_duration:.2f}s (must be < 1.0s)"
    )

    # Wait for Qt signals to propagate to main thread
    qtbot.waitUntil(lambda: finished_called, timeout=1000)
    assert result_received == "cancelled"
