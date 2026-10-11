"""A log that survives a hang, and a watchdog that ends one (L1.2). Engine-free.

A frozen GUI exe has no stdout and no stderr (#291 is the precedent), and a
software rasteriser can take a long time over one frame. The L0 spike's first
Windows evidence run "hung" for 56 minutes with no output; it was not hung. So
every headless render writes a line-flushed log, and may bound itself with a
watchdog that turns a real hang into a failed run with a stack trace.

TWO LINES OF DEFENCE, because the first one is not enough (ADR-048 addendum
L1.2, review round 2 — measured):

1. In the process: ``faulthandler.dump_traceback_later(exit=True)``. Its C thread
   needs no GIL, writes every thread's stack and calls ``_exit(1)``. That ends a
   plain hang, and on Linux also the deadlock below (5 of 5, exit code 1). On
   Windows it does NOT end a process whose threads are deadlocked inside Qt and
   the graphics driver: ``_exit`` is ``ExitProcess`` there, which runs every
   DLL's detach code, and that waited forever — the dump was written and the
   process stayed alive (every run, on the GPU and on WARP).
2. From outside (``supervised=True``, Windows): a second, tiny process waits on
   this one and calls ``TerminateProcess`` when it is still alive ``GRACE_S``
   after the deadline — which no state of this process can prevent (every run
   of the same deadlock: exit code 1). It writes the reason into the log first.

The supervisor can only ever end the process it was started for, and only while
that process is inside the supervised block: it identifies the process by pid
AND creation time, and it leaves at once when the block's release event is
signalled — or is GONE, which is what a block that finished before the
supervisor was up leaves behind.

This file is also that second process: ``python runlog.py <pid> …`` from source,
``<exe> --selftest-supervisor <pid> …`` in the frozen build (``main`` dispatches
to ``supervisor_main``). It imports nothing but the standard library.
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any, Final

#: The frozen exe's command-line flag for the supervising process (``main.main``).
SUPERVISOR_FLAG: Final = "--selftest-supervisor"
#: How long after the in-process deadline the supervisor waits before it ends the
#: process: time for the stack dump to be written.
GRACE_S: Final = 10.0


class RenderLog:
    """Timestamped, line-flushed log — to ``path`` and to stdout when there is one.

    ``log(phase, key=value, …)``. With ``path=None`` it only mirrors to stdout
    (and is silent in a windowed frozen exe, which has none). ``mirror=False``
    keeps stdout for the caller's own summary.
    """

    def __init__(self, path: Path | None = None, *, mirror: bool = True) -> None:
        self.path = path
        self._mirror = mirror
        self._started = time.perf_counter()
        self._file: IO[str] | None = None
        self._crash_file: IO[str] | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")  # a fresh log, then append-only:
            self._file = path.open("a", encoding="utf-8", buffering=1)  # faulthandler appends too

    def __call__(self, phase: str, **fields: Any) -> None:
        detail = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{time.perf_counter() - self._started:9.3f}s [{phase}] {detail}".rstrip()
        if self._file is not None and not self._file.closed:
            self._file.write(line + "\n")
            self._file.flush()
        if self._mirror and sys.stdout is not None:
            with contextlib.suppress(Exception):  # a closed or broken stdout must not fail a render
                print(line, flush=True)

    def crash_file(self) -> IO[str] | None:
        """Where ``faulthandler`` writes: its OWN handle on the log (never closed, so a
        crash in teardown still leaves its stack), else stderr, else None."""
        if self.path is not None:
            if self._crash_file is None:
                self._crash_file = self.path.open("a", encoding="utf-8")
            return self._crash_file
        return sys.stderr

    def close(self) -> None:
        if self._file is not None:
            self._file.close()


def _has_descriptor(stream: IO[str]) -> bool:
    """False for a stream that is not a real file (a captured or replaced stderr)."""
    try:
        stream.fileno()
    except Exception:  # noqa: BLE001 - io.UnsupportedOperation, ValueError (closed), AttributeError
        return False
    return True


@contextlib.contextmanager
def watchdog(seconds: float, log: RenderLog, what: str = "the run", *,
             supervised: bool = False) -> Iterator[None]:
    """Bound the block: after ``seconds`` every thread's stack is dumped into the
    log and the PROCESS exits with code 1 (``faulthandler``). A hang becomes a
    failed run with a stack, not a job that never ends.

    The reason is written to the log in words: when the watchdog is armed (what a
    ``Timeout`` below that line means), and — shortly before the end, from a
    helper thread — that it is about to fire. The helper needs the interpreter
    lock; a hang that holds it silences the helper, never the dump.

    ``supervised=True`` adds the second line of defence (module docstring): a
    process of its own that ends this one from outside when it is still alive
    ``GRACE_S`` after the deadline, and says so in the log. Windows only — that
    is where ``faulthandler``'s exit was measured to fail (on Linux it was measured
    to work); elsewhere the log says that there is none.

    With neither a log file nor a stderr (a windowed exe whose log could not be
    opened) the stacks have nowhere to go — the process is ended all the same.

    For self-tests and scripts that own their process — never inside the running
    app: the exit is not negotiable. ONE watchdog per process at a time
    (``faulthandler`` has a single timer): do not nest. ``seconds <= 0`` switches
    it off.
    """
    if seconds <= 0:
        log("watchdog", armed=False, what=what, reason="disabled")
        yield
        return
    target = log.crash_file()
    nowhere = None
    if target is None or not _has_descriptor(target):  # faulthandler writes to a descriptor
        nowhere = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - closed in the finally
        target = nowhere
    supervisor = start_supervisor(seconds + GRACE_S, log.path, what) if supervised else None
    log("watchdog", armed=True, seconds=seconds, what=what,
        supervisor=(supervisor.describe() if supervisor is not None
                    else ("none" if not supervised else "none (not available here)")),
        note=f"if {what} is not done by then: 'Timeout' and every thread's stack follow "
             "here, and the process exits with code 1"
             + (" (no log file and no stderr: the stacks are lost, the exit is not)"
                if nowhere is not None else ""))

    def announce() -> None:
        with contextlib.suppress(Exception):  # a closed log must not turn into a second failure
            log("watchdog", fired=True, what=what,
                reason=f"{what} did not finish within {seconds:g} s — it hangs in the call at "
                       "the top of the main thread's stack below; ending the process, exit code 1")

    helper = threading.Timer(max(seconds - 1.0, seconds * 0.8), announce)
    helper.daemon = True
    faulthandler.dump_traceback_later(seconds, exit=True, file=target)
    helper.start()
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()
        helper.cancel()
        if supervisor is not None:
            supervisor.release()
        if nowhere is not None:
            nowhere.close()


# ── the supervising process (Windows) ────────────────────────────────────────


class Supervisor:
    """The handle on a started supervising process (``start_supervisor``)."""

    def __init__(self, process: subprocess.Popen[bytes], event: int, seconds: float) -> None:
        self.process = process
        self._event = event
        self._seconds = seconds

    def describe(self) -> str:
        return f"process {self.process.pid}, ends this one {self._seconds:g} s from now"

    def release(self) -> None:
        """The supervised block is over: tell the supervisor to go, at once."""
        _signal_and_close(self._event)
        self._event = 0


def self_command() -> list[str]:
    """How to start THIS code as a process of its own: the frozen exe with the flag
    ``main`` dispatches on, or — from source — this very file (never ``-m
    open_garden_planner``: from a git worktree that could start another checkout's
    application)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, SUPERVISOR_FLAG]
    return [sys.executable, "-P", str(Path(__file__).resolve())]


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _SYNCHRONIZE: Final = 0x00100000
    _PROCESS_TERMINATE: Final = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
    _EVENT_MODIFY_STATE: Final = 0x0002
    _WAIT_OBJECT_0: Final = 0x0
    _WAIT_TIMEOUT: Final = 0x102

    def _kernel32() -> Any:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k32.CreateEventW.restype = wintypes.HANDLE
        k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k32.OpenEventW.restype = wintypes.HANDLE
        k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k32.SetEvent.argtypes = [wintypes.HANDLE]
        k32.WaitForMultipleObjects.restype = wintypes.DWORD
        k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                                               wintypes.BOOL, wintypes.DWORD]
        k32.TerminateProcess.restype = wintypes.BOOL
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        return k32

    def _created(k32: Any, handle: Any) -> int:
        """When the process was created (100 ns units): with the pid, its identity."""
        times = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return 0
        return (int(times[0].dwHighDateTime) << 32) | int(times[0].dwLowDateTime)

    def _event_name(pid: int, created: int) -> str:
        return f"Local\\ogp-supervised-{pid}-{created}"

    def _signal_and_close(event: int) -> None:
        if event:
            k32 = _kernel32()
            k32.SetEvent(wintypes.HANDLE(event))
            k32.CloseHandle(wintypes.HANDLE(event))

    def start_supervisor(seconds: float, log_path: Path | None, what: str) -> Supervisor | None:
        """Start the process that ends this one ``seconds`` from now unless ``release()`` is
        called first. None when it cannot be started — the caller says so in its log."""
        try:
            k32 = _kernel32()
            pid = os.getpid()
            created = _created(k32, k32.GetCurrentProcess())
            event = k32.CreateEventW(None, True, False, _event_name(pid, created))
            if not event:
                return None
            process = subprocess.Popen(  # noqa: S603 - our own code, fixed arguments
                # "what:" keeps a name like "--selftest" from reading as a flag of its own
                [*self_command(), str(pid), str(created), f"{seconds:g}",
                 str(log_path) if log_path is not None else "-", f"what:{what}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
        except Exception:  # noqa: BLE001 - no supervisor is a degraded run, not a crash
            return None
        return Supervisor(process, int(event), seconds)

    def supervisor_main(arguments: list[str]) -> int:
        """The supervising process. ``arguments``: pid, creation time, seconds, log path
        (``-`` for none), what. It ends when the supervised process ends, when it is
        released, or — after ``seconds`` — by terminating that process."""
        try:
            pid, created, seconds = int(arguments[0]), int(arguments[1]), float(arguments[2])
            log_path = arguments[3]
            what = arguments[4].removeprefix("what:") if len(arguments) > 4 else "the run"
        except (IndexError, ValueError):
            return 2
        k32 = _kernel32()
        process = k32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return 0  # it is gone already
        try:
            if _created(k32, process) != created:
                return 0  # another process has that pid now: not ours to touch
            event = k32.OpenEventW(_SYNCHRONIZE | _EVENT_MODIFY_STATE, False, _event_name(pid, created))
            if not event:
                # The block created the event before it started us, and destroys it when it
                # ends. No event: the block is over already (a fast block beats our start).
                # Waiting on the process alone here would END A PROCESS THAT FINISHED ITS
                # BLOCK and simply lives on — a test runner, a tool.
                return 0
            try:
                handles = (wintypes.HANDLE * 2)(process, event)
                outcome = k32.WaitForMultipleObjects(2, handles, False, int(seconds * 1000))
            finally:
                k32.CloseHandle(event)
            if outcome != _WAIT_TIMEOUT:
                return 0  # it ended by itself, or released us
            if log_path != "-":
                with contextlib.suppress(OSError), open(log_path, "a", encoding="utf-8") as log:
                    log.write(
                        f"          [supervisor] fired=True what={what} reason={what} was still "
                        f"alive {seconds:g} s after its watchdog was armed — its own watchdog "
                        "could not end the process (see the stack dump above, if any); "
                        "terminated from outside, exit code 1\n")
            k32.TerminateProcess(process, 1)
            return 0
        finally:
            k32.CloseHandle(process)

else:

    def _signal_and_close(event: int) -> None:  # noqa: ARG001 - the Windows twin's signature
        return None

    def start_supervisor(seconds: float, log_path: Path | None, what: str) -> Supervisor | None:  # noqa: ARG001
        """Not needed here: ``_exit`` ends the process at once — measured on Linux in the
        very deadlock the Windows supervisor exists for (module docstring)."""
        return None

    def supervisor_main(arguments: list[str]) -> int:  # noqa: ARG001
        return 2


if __name__ == "__main__":
    sys.exit(supervisor_main(sys.argv[1:]))
