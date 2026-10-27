"""Main-thread marshaling boundary for the Agent API.

The embedded MCP server (see :mod:`open_garden_planner.agent_api.server`) runs on
a background thread; its tool handlers must NOT touch Qt objects directly because
Qt is not thread-safe. :class:`MainThreadBridge` lets a worker thread run a
callable ON the Qt main thread and get the result (or exception) back
synchronously.

**House rule** (verified against mcp 1.28.1 ``func_metadata`` tool dispatch): the
MCP SDK calls a *synchronous* tool handler **inline on the asyncio event loop**;
only ``async def`` handlers yield the loop. So a Qt-touching tool must NOT call
:meth:`run_on_main` from a sync handler — that would block the uvicorn loop on
``Future.result()``. Instead, declare the tool ``async def`` and offload the
blocking call to a worker thread, e.g.::

    @mcp.tool()
    async def get_plan_summary() -> PlanSummary:
        return await anyio.to_thread.run_sync(snapshot_provider)  # provider calls run_on_main

That worker thread then blocks in :meth:`run_on_main` while the event loop stays
free. :meth:`abort_pending` lets shutdown unblock waiting workers immediately.
Its error distinguishes a queued callable that will not run from one that
already started and may still finish on Qt's thread.

This bridge is the reusable, write-ready core: later phases route edits through
``run_on_main`` exactly the same way reads do.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from typing import Any, TypeVar

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal

T = TypeVar("T")

#: Default time a worker waits for the main thread to service a queued call.
DEFAULT_TIMEOUT_S = 5.0


class BridgeClosedError(RuntimeError):
    """Raised in a worker when shutdown aborts its main-thread call."""


class MainThreadBridge(QObject):
    """Run callables on the Qt main thread from any thread, synchronously."""

    # Carry the callable + its Future as opaque objects so PyQt does not try to
    # marshal them through the C++ type system.
    _invoke = pyqtSignal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._pending: set[Future[Any]] = set()
        # QueuedConnection => the slot runs in the thread that owns this QObject
        # (the main thread), regardless of which thread emits the signal.
        self._invoke.connect(  # type: ignore[call-arg]
            self._on_invoke, Qt.ConnectionType.QueuedConnection
        )

    def run_on_main(self, fn: Callable[[], T], *, timeout: float = DEFAULT_TIMEOUT_S) -> T:
        """Execute ``fn`` on the main thread and return its result.

        If called from the main thread, ``fn`` runs inline (no event-loop hop).
        Otherwise the call is queued to the main thread and this method blocks
        (up to ``timeout`` seconds) for the result. Exceptions raised by ``fn``
        propagate to the caller; a timeout raises
        ``concurrent.futures.TimeoutError``; an :meth:`abort_pending` during the
        wait raises :class:`BridgeClosedError`.
        """
        if QThread.currentThread() is self.thread():
            return fn()
        future: Future[T] = Future()
        with self._lock:
            self._pending.add(future)
        try:
            self._invoke.emit(fn, future)
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            # A wait timeout does NOT finish a Future. Without cancellation a
            # queued write would still execute when Qt resumed, after the client
            # had already seen a failure (and might have retried it).
            if future.done():
                message = str(exc) or "Qt operation raised TimeoutError; inspect application state before retrying"
            elif future.cancel():
                message = "Qt main-thread timeout: operation cancelled before execution; no changes applied"
            else:
                # set_running_or_notify_cancel won the race. We cannot roll
                # back a function already running on Qt's thread.
                message = "Qt operation started before timeout and may have completed; inspect application state before retrying"
            raise TimeoutError(message) from exc
        finally:
            with self._lock:
                self._pending.discard(future)

    def abort_pending(self) -> None:
        """Fail every waiting :meth:`run_on_main` call immediately.

        Called on shutdown so blocked worker threads return at once, letting the
        server's event loop process ``should_exit`` without waiting on the main
        thread (which is busy tearing down). A callable still queued has not
        run and is marked as cancelled. A callable already running cannot be
        stopped; its worker is told to inspect current state or the operation
        receipt before retrying. Safe to call repeatedly.
        """
        with self._lock:
            for future in self._pending:
                if not future.done():
                    # Completion/cancellation can race with shutdown.
                    with contextlib.suppress(InvalidStateError):
                        if future.running():
                            message = (
                                "Agent API shutting down: Qt operation already started and "
                                "may have completed; inspect current state or its operation "
                                "receipt before retrying"
                            )
                        else:
                            message = (
                                "Agent API shutting down: Qt operation cancelled before "
                                "execution; no changes applied"
                            )
                        future.set_exception(BridgeClosedError(message))

    def _on_invoke(self, fn: Callable[[], Any], future: Future[Any]) -> None:
        """Run a queued callable on the main thread and resolve its future."""
        with self._lock:
            if future.done() or not future.set_running_or_notify_cancel():
                return  # Aborted/cancelled before execution; no late writes.
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 - propagate to the caller thread
            # InvalidStateError: raced with abort_pending; the caller already saw
            # BridgeClosedError, so dropping this result is correct.
            with contextlib.suppress(InvalidStateError):
                future.set_exception(exc)
        else:
            with contextlib.suppress(InvalidStateError):
                future.set_result(result)
