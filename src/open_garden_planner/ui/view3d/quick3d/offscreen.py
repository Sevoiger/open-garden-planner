"""Records in, one ``QImage`` out — without showing a window (L1.2). ENGINE module.

``render_records`` runs the real pipeline — ``SceneSync`` → ``Quick3DSink`` →
``Quick3DHost`` — on a host that is never shown, and reads the frame back. It is
what the render tests, the frozen exe's ``--selftest`` and later tools call.

What it guarantees:

- **One host per frame.** A never-shown Qt Quick window renders exactly one
  frame (``host.py``); every call builds its own host and closes it.
- **The clock stops at the finished READBACK** (``open_ms``), never at a frame
  being submitted: on a software rasteriser the real cost lands in the readback.
- **Errors are loud.** Every Qt message of the render is recorded; a QML error or
  a shader that does not compile raises ``RenderError`` — the frame would still
  have rendered, in the wrong colours (``qt_messages.py``).
- **It cannot hang silently.** ``RenderLog`` is flushed per line, and
  ``watchdog`` dumps every thread's stack into it and exits the process with a
  non-zero code when the time is up. A frozen GUI exe has no stdout and no
  stderr; the log file is the only witness.
"""

from __future__ import annotations

import contextlib
import faulthandler
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import numpy as np
from numpy.typing import NDArray
from PyQt6.QtGui import QGuiApplication, QImage

from open_garden_planner.core.scene3d import (
    BuilderRegistry,
    GroundSpec,
    Record,
    SceneSync,
    SunState,
)

from . import qt_messages
from .adapter import Quick3DSink
from .host import Quick3DHost
from .look import NEUTRAL_DAY, Look
from .shots import Shot


class RenderError(RuntimeError):
    """An offscreen render that must not be trusted (QML or shader error, no image)."""


class RenderLog:
    """Timestamped, line-flushed log — to ``path`` and to stdout when there is one.

    ``log(phase, key=value, …)``. With ``path=None`` it only mirrors to stdout
    (and is silent in a windowed frozen exe, which has none).
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
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
        if sys.stdout is not None:
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


@contextlib.contextmanager
def watchdog(seconds: float, log: RenderLog) -> Iterator[None]:
    """Bound the block: after ``seconds`` every thread's stack is dumped into the
    log and the PROCESS exits with code 1 (``faulthandler``). A hang becomes a
    failed run with a stack, not a job that never ends."""
    target = log.crash_file() if seconds > 0 else None
    if target is None:
        log("watchdog", armed=False,
            reason="disabled" if seconds <= 0 else "no log file and no stderr to dump into")
        yield
        return
    log("watchdog", armed=True, seconds=seconds,
        note="on timeout: 'Timeout' and all thread stacks follow, exit code 1")
    faulthandler.dump_traceback_later(seconds, exit=True, file=target)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


_APPLICATION: list[QGuiApplication] = []  # keeps an application we had to create alive


def ensure_application() -> QGuiApplication:
    """The running application, or a new ``QApplication`` when there is none
    (a self-test, a script). Never call anything of Qt Quick 3D before this:
    ``QQuick3D.idealSurfaceFormat()`` without an application segfaults on Linux."""
    app = QGuiApplication.instance()
    if app is None:
        from PyQt6.QtWidgets import QApplication

        app = QApplication(sys.argv[:1])
        _APPLICATION.append(app)
    return app


def image_rgba(image: QImage) -> NDArray[np.uint8]:
    """A ``QImage`` as an ``(height, width, 4)`` uint8 array, RGBA, row 0 on top."""
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    bits = converted.constBits()
    bits.setsize(converted.sizeInBytes())
    rows = np.frombuffer(bytes(bits), np.uint8).reshape(
        converted.height(), converted.bytesPerLine() // 4, 4)
    return rows[:, :converted.width(), :].copy()


@dataclass(frozen=True, slots=True)
class OffscreenFrame:
    """One rendered frame and what is known about how it came to be."""

    image: QImage
    #: Request → finished readback, milliseconds (QML load, builders, uploads, the frame).
    open_ms: float
    breakdown_ms: Mapping[str, float]
    graphics_api: str
    #: The device Qt rendered on, when ``graphics_info`` was asked for — e.g.
    #: ``NVIDIA GeForce RTX 4070``, ``Microsoft Basic Render Driver`` (WARP), ``llvmpipe …``.
    adapter: str | None
    device_pixel_ratio: float
    view_size: tuple[float, float]
    items: int
    parts: int
    #: ``QtMessages.report()``: counts per class, the errors, warnings, environment.
    qt: Mapping[str, Any] = field(default_factory=dict)

    def rgba(self) -> NDArray[np.uint8]:
        return image_rgba(self.image)


def render_records(
    records: Mapping[str, Record],
    *,
    shot: Shot,
    sun: SunState | None = None,
    ground: GroundSpec | None = None,
    size: tuple[int, int] = (640, 360),
    registry: BuilderRegistry | None = None,
    look: Look = NEUTRAL_DAY,
    qml_dir: Path | None = None,
    log: RenderLog | None = None,
    graphics_info: bool = False,
) -> OffscreenFrame:
    """Render ``records`` from ``shot`` under ``sun`` into an image of ``size``
    (logical pixels), through the real ``SceneSync`` → ``Quick3DSink`` path.

    ``sun=None``: no sunlight (fill only). ``ground=None``: the endless meadow
    without a plan rectangle. ``graphics_info=True`` also reports the graphics
    adapter (it switches Qt's ``qt.rhi.general`` debug output on — for
    self-tests and scripts, not inside the running app).

    Raises ``RenderError`` on a QML or shader error and when no image comes
    back. A blank-looking frame is NOT judged here: the caller knows what the
    frame must contain.
    """
    log = log if log is not None else RenderLog()
    started = time.perf_counter()
    breakdown: dict[str, float] = {}
    ensure_application()
    messages = qt_messages.QtMessages(log)
    previous = qt_messages.install(messages)
    if graphics_info:
        qt_messages.enable_graphics_info()
    host: Quick3DHost | None = None
    try:
        host = Quick3DHost(size, qml_dir=qml_dir, look=look, log=log)
        breakdown["qml_load"] = host.qml_load_ms
        mark = time.perf_counter()
        sink = Quick3DSink(host)
        sync = SceneSync(sink, registry)
        sync.apply(records)
        if ground is not None:
            sync.set_ground(ground)
        if sun is not None:
            sync.set_sun(sun)
        host.set_shot(shot)
        breakdown["scene_apply"] = (time.perf_counter() - mark) * 1000.0
        parts = sum(sink.part_count(item_id) for item_id in sink.item_ids())
        log("scene_applied", ms=round(breakdown["scene_apply"], 1), items=len(sink.item_ids()),
            parts=parts, build_failures=len(sync.failures))
        mark = time.perf_counter()
        image = host.grab("offscreen")
        breakdown["grab"] = (time.perf_counter() - mark) * 1000.0
        open_ms = (time.perf_counter() - started) * 1000.0
        frame = OffscreenFrame(
            image=image, open_ms=open_ms, breakdown_ms=breakdown,
            graphics_api=host.graphics_api(),
            adapter=qt_messages.graphics_adapter(messages.info_lines()),
            device_pixel_ratio=host.device_pixel_ratio(), view_size=host.view_size(),
            items=len(sink.item_ids()), parts=parts, qt=messages.report(),
        )
        log("rendered", open_ms=round(open_ms, 1), api=frame.graphics_api, adapter=frame.adapter,
            px=f"{image.width()}x{image.height()}", qt=messages.report()["counts"])
    except Exception as error:
        log("render_failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        if host is not None:
            host.close()
        # Detach the recorder before anything can close the log it writes to: a Qt
        # message handler that raises is turned into qFatal by PyQt.
        qt_messages.uninstall(previous)
    if messages.errors:
        raise RenderError(
            f"{len(messages.errors)} Qt error(s) during the render — the frame is not the one "
            f"the QML and its shaders describe. First: {messages.errors[0]}")
    return frame
