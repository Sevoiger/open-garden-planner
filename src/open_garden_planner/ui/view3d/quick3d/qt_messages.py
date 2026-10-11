"""Every Qt message of a render, recorded; a shader or QML error fails it (L1.2).

Ported from the L0 spike (``spike_q3d/qt_messages.py``, ADR-048 entry 17).
Why it exists: a custom shader that does not compile is only a ``QtWarningMsg``
— ``QSpirvCompiler: Failed to parse shader`` goes to stderr, the frame still
renders, in another colour. Measured in the spike: one renamed call in
``water.frag`` changed 449 pond pixels, exit code 0. A frozen GUI exe has no
stderr at all, so the only way to notice is to listen.

Four classes:

- ``error`` — the frame on screen is not the frame the QML and its shaders
  describe (shader compile/parse/link, QML load and binding errors, anything Qt
  itself calls critical or fatal). A render with one of these FAILS.
- ``warning`` — any other Qt warning. A clean render has none.
- ``environment`` — warnings measured to come from the machine, not from the
  render (``_ENVIRONMENT``, exact and short on purpose). Counted and reported,
  never a failure.
- ``info`` — debug and info output (the graphics adapter is read from it).

The LEVEL decides first: only a warning can be promoted to ``error`` by its
text. Debug and info output is never an error, whatever it says — the self-test
switches Qt's ``qt.rhi.general`` debug output on, and the wording of a Qt debug
line must not be able to fail a release.

The classifier and the recorder are Qt-free (unit-tested without a GPU); only
``install`` / ``uninstall`` / ``graphics_info`` touch Qt — ``QtCore``, which is
not an engine module.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sys
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Final

# A message matching one of these means the frame on screen is not the frame the
# QML and its shaders describe: a shader that did not compile, a QML error.
_ERROR: Final = re.compile(
    r"QSpirvCompiler|QShaderBaker|Failed to (compile|parse|link|build|load)"
    r"|shader[^\n]*(fail|error)|\.(frag|vert|glsl):\d+"
    r"|\.qml:\d+(:\d+)?: |QQmlComponent|ReferenceError|TypeError"
    r"|is not a type|non-existent property|Cannot assign",
    re.IGNORECASE,
)

# Warnings of the MACHINE, each measured, each (category, pattern) exact. Checked
# AFTER the error rules, so nothing here can hide a broken shader. Do not widen an
# entry to a whole category: an allow-list wider than what was measured is a hole.
_ENVIRONMENT: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # Qt 6.11 on Windows asks SetupAPI for the monitor's EDID when a window opens;
    # with the display asleep (the L1.2 dev box, 2026-10-11) that fails on every run:
    #   qt.qpa.screen: "Unable to open monitor interface to \\.\DISPLAY1:" "Unknown error 0xe0000225."
    ("qt.qpa.screen", re.compile(r'^"?Unable to open monitor interface to ')),
)

_KEEP: Final = 100  # per class: enough to read, bounded for a long session
CLASSES: Final = ("error", "warning", "environment", "info")

_ADAPTER: Final = re.compile(r"Adapter \d+: '([^']*)'")
_GRAPHICS_INFO_DEPTH: Final = [0]  # nesting depth of graphics_info() blocks (GUI thread)
_GL_RENDERER: Final = re.compile(r"OpenGL VENDOR: .*? RENDERER: (.*?) VERSION: ")


def classify(kind: str, text: str, category: str = "") -> str:
    """``"error"``, ``"warning"``, ``"environment"`` or ``"info"`` for one Qt message.

    ``kind`` is the Qt message type in lower case (``debug``, ``info``,
    ``warning``, ``critical``, ``fatal``).
    """
    if kind in ("critical", "fatal"):
        return "error"
    if kind != "warning":
        return "info"  # debug / info output is never an error, whatever its wording
    if _ERROR.search(text):
        return "error"
    for wanted, pattern in _ENVIRONMENT:
        if category == wanted and pattern.search(text):
            return "environment"
    return "warning"


class QtMessages:
    """Thread-safe record of Qt messages (Qt may call from its render thread)."""

    def __init__(self, log: Callable[..., None] | None = None) -> None:
        self._log = log
        self._lock = threading.Lock()
        self.counts: dict[str, int] = dict.fromkeys(CLASSES, 0)
        self.kept: dict[str, list[str]] = {name: [] for name in CLASSES}

    def add(self, kind: str, text: str, category: str = "") -> None:
        """Record one message. Never raises: it runs inside Qt's message handler,
        where PyQt turns an exception into ``qFatal`` — an abort (the spike
        reproduced exit 134 with a write to a closed log)."""
        try:
            cls = classify(kind, text, category)
            line = f"{kind}: {category + ': ' if category else ''}{text}"
            with self._lock:
                self.counts[cls] += 1
                n = self.counts[cls]
                if len(self.kept[cls]) < _KEEP:
                    self.kept[cls].append(line[:600])
            if self._log is not None and cls != "info" and n <= _KEEP:
                suffix = " (further ones only counted)" if n == _KEEP else ""
                self._log("qt", level=cls, msg=line[:300].replace("\n", " ") + suffix)
        except Exception:  # noqa: BLE001, S110 - see the docstring
            pass

    @property
    def errors(self) -> list[str]:
        with self._lock:
            return list(self.kept["error"])

    @property
    def warnings(self) -> list[str]:
        with self._lock:
            return list(self.kept["warning"])

    @property
    def clean(self) -> bool:
        """No error and no warning (environment and info do not count)."""
        with self._lock:
            return self.counts["error"] == 0 and self.counts["warning"] == 0

    def info_lines(self) -> list[str]:
        with self._lock:
            return list(self.kept["info"])

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {"counts": dict(self.counts), "errors": list(self.kept["error"]),
                    "warnings": list(self.kept["warning"][:30]),
                    "environment": list(self.kept["environment"][:30])}


def logging_sink(logger: logging.Logger) -> Callable[..., None]:
    """A recorder callback that writes to a Python logger — for a LIVE view, whose
    Qt warnings and errors belong in the app's log (``Quick3DHost.watch_messages``).

    An ``error`` becomes ``logger.error``, a ``warning`` ``logger.warning``, an
    ``environment`` message ``logger.info``. Safe from any thread (``logging`` is).
    """
    levels = {"error": logging.ERROR, "warning": logging.WARNING}

    def log(_phase: str, *, level: str = "info", msg: str = "", **_fields: Any) -> None:
        logger.log(levels.get(level, logging.INFO), "Qt: %s", msg)

    return log


def graphics_adapter(info_lines: Iterable[str]) -> str | None:
    """The graphics device Qt chose, read from ``qt.rhi.general`` (see
    ``graphics_info``) — or None when the lines do not say.

    Direct3D lists every adapter and marks one with ``using this adapter``; the
    marker follows the adapter it means. OpenGL prints the context's
    ``RENDERER``. The last device created wins.
    """
    chosen: str | None = None
    last_listed: str | None = None
    for line in info_lines:
        if (match := _ADAPTER.search(line)) is not None:
            last_listed = match.group(1)
        elif "using this adapter" in line:
            if last_listed is not None:
                chosen = last_listed
        elif (match := _GL_RENDERER.search(line)) is not None:
            chosen = match.group(1).strip()
    return chosen


@contextlib.contextmanager
def graphics_info() -> Iterator[None]:
    """Make Qt name its graphics device (``qt.rhi.general``) for the block — as debug
    output, which the recorder files under ``info`` — and switch it off again after.
    Enter it before the Quick window is created.

    ``QLoggingCategory.setFilterRules`` holds ONE rule set per process and has no
    getter: entering replaces rules set the same way, leaving clears them. Nothing
    in the app sets any (2026-10); a caller that does must not use this. Blocks
    may nest (a recorded run around a recorded frame): the outermost one switches.
    """
    from PyQt6.QtCore import QLoggingCategory

    if _GRAPHICS_INFO_DEPTH[0] == 0:
        QLoggingCategory.setFilterRules("qt.rhi.general.debug=true")
    _GRAPHICS_INFO_DEPTH[0] += 1
    try:
        yield
    finally:
        _GRAPHICS_INFO_DEPTH[0] -= 1
        if _GRAPHICS_INFO_DEPTH[0] == 0:
            QLoggingCategory.setFilterRules("")


def install(messages: QtMessages) -> Any:
    """Route every Qt message through ``messages``, then on to whoever listened
    before (a recorder further out, the app's own handler) — or to stderr when
    nobody did. Returns that previous handler: give it to ``uninstall``."""
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    names = {QtMsgType.QtDebugMsg: "debug", QtMsgType.QtInfoMsg: "info",
             QtMsgType.QtWarningMsg: "warning", QtMsgType.QtCriticalMsg: "critical",
             QtMsgType.QtFatalMsg: "fatal"}
    outer: list[Any] = [None]

    def handler(mode: Any, context: Any, text: str | None) -> None:
        category = getattr(context, "category", None) or ""
        if isinstance(category, bytes):
            category = category.decode("utf-8", "replace")
        if category == "default":
            category = ""
        kind = names.get(mode, "warning")
        messages.add(kind, text or "", category)
        # never raise in here: PyQt turns an exception in a message handler into qFatal
        with contextlib.suppress(Exception):
            if outer[0] is not None:
                outer[0](mode, context, text)
            elif sys.stderr is not None and kind != "debug":
                print(text, file=sys.stderr, flush=True)

    outer[0] = qInstallMessageHandler(handler)
    return outer[0]


def uninstall(previous: Any = None) -> None:
    """Put ``previous`` back (Qt's default handler when None) — BEFORE the log the
    recorder writes to closes."""
    from PyQt6.QtCore import qInstallMessageHandler

    qInstallMessageHandler(previous)
