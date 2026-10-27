"""Unit tests for the Agent API main-thread marshaling bridge.

The bridge lets a worker thread run a callable on the Qt main thread and get the
result back. These tests drive it from a real worker thread while the main test
thread pumps the Qt event loop via ``qtbot``.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from PyQt6.QtCore import QCoreApplication, QThread

from open_garden_planner.agent_api.bridge import BridgeClosedError, MainThreadBridge


class TestMainThreadBridge:
    def test_runs_callable_on_main_thread(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        main_thread = QThread.currentThread()
        captured: dict[str, Any] = {}

        def worker() -> None:
            captured["ran_on"] = bridge.run_on_main(
                QThread.currentThread, timeout=5.0
            )
            captured["worker_thread"] = QThread.currentThread()

        thread = threading.Thread(target=worker)
        thread.start()
        qtbot.waitUntil(lambda: "ran_on" in captured, timeout=5000)
        thread.join(timeout=5.0)

        assert captured["ran_on"] is main_thread
        assert captured["worker_thread"] is not main_thread

    def test_same_thread_fast_path(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        # Called directly on the main thread: runs inline, no event loop hop.
        assert bridge.run_on_main(lambda: 21 * 2) == 42

    def test_exception_propagates_to_caller(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        captured: dict[str, Any] = {}

        def worker() -> None:
            try:
                bridge.run_on_main(lambda: 1 / 0)
            except ZeroDivisionError as exc:
                captured["error"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        qtbot.waitUntil(lambda: "error" in captured, timeout=5000)
        thread.join(timeout=5.0)

        assert isinstance(captured["error"], ZeroDivisionError)

    def test_timeout_when_loop_not_pumped(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        captured: dict[str, Any] = {}

        def worker() -> None:
            try:
                # The main thread is blocked on join() below (no event
                # processing), so the queued call cannot complete and must
                # time out.
                bridge.run_on_main(lambda: 1, timeout=0.1)
            except FutureTimeoutError as exc:
                captured["timeout"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5.0)

        assert "timeout" in captured

    def test_timed_out_queued_write_never_executes_later(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        mutations = []
        captured = {}

        def worker():
            try:
                bridge.run_on_main(lambda: mutations.append("late write"), timeout=0.02)
            except FutureTimeoutError as exc:
                captured["error"] = str(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        QCoreApplication.processEvents()  # Deliver the queued signal after the caller saw failure.
        assert mutations == []
        assert "cancelled before execution" in captured["error"]

    def test_timeout_after_execution_starts_reports_indeterminate_outcome(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        mutations = []
        captured = {}
        operation_started = threading.Event()
        caller_timed_out = threading.Event()

        def operation():
            operation_started.set()
            # Hold the Qt callback until the caller has observed its timeout.
            # This pins the "already started" branch without depending on two
            # sleeps racing on a loaded test machine.
            assert caller_timed_out.wait(timeout=5.0)
            mutations.append("one write")

        def worker():
            try:
                bridge.run_on_main(operation, timeout=0.1)
            except FutureTimeoutError as exc:
                captured["error"] = str(exc)
                caller_timed_out.set()

        thread = threading.Thread(target=worker)
        thread.start()
        qtbot.waitUntil(operation_started.is_set, timeout=2000)
        qtbot.waitUntil(lambda: "error" in captured, timeout=2000)
        thread.join(timeout=2)
        assert mutations == ["one write"]
        assert "may have completed" in captured["error"]

    def test_abort_pending_cancels_queued_call_without_late_write(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        captured: dict[str, Any] = {}
        mutations: list[str] = []

        def worker() -> None:
            try:
                # Long timeout: only abort_pending should release this. The main
                # thread below deliberately never pumps the Qt loop, so the
                # queued call is not serviced normally.
                bridge.run_on_main(lambda: mutations.append("late write"), timeout=30.0)
            except BridgeClosedError as exc:
                captured["aborted"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        # Wait until the worker has registered its pending future WITHOUT pumping
        # Qt events (time.sleep, not qtbot.wait), so the call cannot complete
        # normally before we abort it.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with bridge._lock:  # noqa: SLF001 - white-box check of pending state
                if bridge._pending:
                    break
            time.sleep(0.01)
        bridge.abort_pending()
        thread.join(timeout=5.0)
        QCoreApplication.processEvents()  # Drain the queued callback after aborting its Future.

        error = captured.get("aborted")
        assert isinstance(error, BridgeClosedError)
        assert "cancelled before execution" in str(error)
        assert "no changes applied" in str(error)
        assert mutations == []

    def test_abort_running_call_reports_indeterminate_outcome(self, qtbot: Any) -> None:
        bridge = MainThreadBridge()
        captured: dict[str, Any] = {}
        operation_started = threading.Event()
        caller_returned = threading.Event()
        release_operation = threading.Event()
        mutations: list[str] = []

        def operation() -> None:
            operation_started.set()
            assert release_operation.wait(timeout=5.0)
            mutations.append("write completed after abort")

        def worker() -> None:
            try:
                bridge.run_on_main(operation, timeout=30.0)
            except BridgeClosedError as exc:
                captured["error"] = str(exc)
                caller_returned.set()

        def aborter() -> None:
            assert operation_started.wait(timeout=5.0)
            bridge.abort_pending()
            # Ensure the worker has observed the error before allowing the
            # already-running Qt callable to finish its mutation.
            assert caller_returned.wait(timeout=5.0)
            release_operation.set()

        abort_thread = threading.Thread(target=aborter)
        call_thread = threading.Thread(target=worker)
        abort_thread.start()
        call_thread.start()
        qtbot.waitUntil(lambda: "error" in captured, timeout=5000)
        call_thread.join(timeout=2.0)
        abort_thread.join(timeout=2.0)

        assert not call_thread.is_alive()
        assert not abort_thread.is_alive()
        assert "already started and may have completed" in captured["error"]
        assert "inspect current state or its operation receipt" in captured["error"]
        assert mutations == ["write completed after abort"]
