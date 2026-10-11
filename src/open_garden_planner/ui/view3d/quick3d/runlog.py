"""A log that survives a hang, and a watchdog that ends one (L1.2). Engine-free.

A frozen GUI exe has no stdout and no stderr (#291 is the precedent), and a
software rasteriser can take a long time over one frame. The L0 spike's first
Windows evidence run "hung" for 56 minutes with no output; it was not hung. So
every headless render writes a line-flushed log, and may bound itself with a
watchdog that turns a real hang into a failed run with a stack trace.
"""

from __future__ import annotations

import contextlib
import faulthandler
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any


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
def watchdog(seconds: float, log: RenderLog, what: str = "the run") -> Iterator[None]:
    """Bound the block: after ``seconds`` every thread's stack is dumped into the
    log and the PROCESS exits with code 1 (``faulthandler``). A hang becomes a
    failed run with a stack, not a job that never ends.

    The reason is written to the log in words, twice over: when the watchdog is
    armed (what a ``Timeout`` below that line means), and — a second before the
    end, from a helper thread — that it is about to fire. The helper needs the
    interpreter lock; a hang inside a C call that holds it silences the helper,
    never the dump and the exit, which ``faulthandler`` does from C.

    With neither a log file nor a stderr (a windowed exe whose log could not be
    opened) the stacks have nowhere to go — the process is ended all the same:
    a run that cannot say why it hangs must still not hang.

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
    log("watchdog", armed=True, seconds=seconds, what=what,
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
        if nowhere is not None:
            nowhere.close()
