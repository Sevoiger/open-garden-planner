"""``Quick3DHost`` — the ``QQuickView`` that shows the garden (L1.2). ENGINE module.

One object owns the Qt Quick window, the loaded ``qml/Garden.qml`` and every
engine ``Model``. It is the ``EngineScene`` a ``Quick3DSink`` drives, and it is
what the workspace embeds (L1.3: ``container()``) and what a render reads back
(``grab()``).

Host choice (ADR-048 criterion 3): a ``QQuickView`` in a
``QWidget.createWindowContainer`` — never a ``QQuickWidget``, which moves the
whole top-level window onto RHI composition. Create it once and keep it: hide
and show it, never rebuild it per open.

THE STATE MACHINE (ADR-048 addendum L1.2). A Qt Quick window can be asked for a
frame in situations where answering kills the process or returns a picture that
is not the scene, so the host never guesses. ``state`` is derived on every call
from what Qt reports (``_facts()`` → ``lifecycle.state_of``, a pure function with
the whole truth table in the unit tests), and every public method either answers
truthfully or raises ``HostStateError``.

==========  ============================================================  ===================
state       what Qt says                                                  a frame?
==========  ============================================================  ===================
``NEW``     no platform window was ever created                           ONE, offscreen
``STARTING``  shown, with an area, not live yet (first expose pending,    no — ``wait_ready()``
            or the next one, or the root item is catching up a resize)
``LIVE``    shown, with an area, scene graph up, exposed, root item at    yes: ``grab``, ``pick``
            the window's size
``HIDDEN``  it has (had) a platform window and shows nothing: not          no
            visible, minimised, or no area (a collapsed splitter pane)
``SPENT``   took its one offscreen frame                                   never again
``CLOSED``  ``close()`` ran, or Qt destroyed the view                      —
==========  ============================================================  ===================

Measured, Qt 6.11 (Direct3D 11 on an RTX 4070 and on WARP; the xcb fact on
GitHub's Linux runner):

- ``grabWindow()`` on a window WITHOUT a scene graph renders one frame offscreen
  and tears everything down. For a window whose platform window was never
  created that is the one offscreen frame (correct, pixel-identical to a shown
  grab); a second one is the bare background. For a window that IS shown and
  waits for its first expose — or is shown afterwards — the process dies in the
  next event-loop pass (abort, access violation or heap corruption). On xcb the
  same death follows a window that was shown and hidden again before any event.
  Hence: only ``NEW`` has the offscreen frame, ``STARTING`` refuses to grab,
  ``SPENT`` refuses ``show()`` and ``container()``, and an embedded host has no
  offscreen frame at all (its parent may show it at any time).
- A window closed by the user keeps its scene graph and loses its platform
  window; a raw grab of it is an access violation. It reads ``HIDDEN``.
- A ``QSplitter`` pane collapsed to width 0 leaves the window visible, exposed
  and 0 px wide: a raw grab returns a blank image of the OLD size and a pick
  returns a bogus hit. After every resize the QML root item lags the window by
  one pass of the event loop. ``LIVE`` therefore needs an area and a root item
  that has caught up; ``wait_ready()`` pumps until then.
- Under the SOFTWARE scene graph (``QT_QPA_PLATFORM=offscreen``) Qt Quick 3D
  draws nothing. ``renders_3d()`` is False there, and ``grab`` / ``pick`` /
  ``project`` raise ``EngineError``: a frame without the garden must not pass
  for one.

Do not wait for ``frameSwapped`` to be sure of a frame: on Windows, update
requests come from a display-vblank thread, and with the monitor asleep a live
window presents its first frame and then none. ``grab()`` on a ``LIVE`` host
renders the current state synchronously — it is the synchronisation point.

A closed host: ``destroy_part``, ``clear`` and ``close`` are no-ops (they release
what is gone already), ``state`` / ``is_closed`` / ``is_visible`` / ``health``
answer, and every other call raises ``HostStateError``.

TROUBLE IN A LIVE VIEW — ``health()``, on the GUI thread. A shown view has
nobody to raise to, and a frozen exe has no stderr. What Qt itself reports on
the GUI thread is collected and logged (Python ``logging``, hence ``app.log``):
QML warnings (the engine's ``warnings`` signal), a graphics device that could
not be created (``sceneGraphError`` — the host CONNECTS it: with nothing
connected Qt shows a modal error box and then aborts the process, measured:
exit ``0xC0000409``), and how many frames were presented.

There is deliberately NO Qt message recorder for a shown host. Qt renders on a
thread of its own (the threaded render loop — measured on Direct3D 11, GPU and
WARP); PyQt's message-handler trampoline needs the GIL; and a render-thread
message that arrives while the GUI thread sits in a Python-called ``hide()``
deadlocks the process for good (5 of 5). Shader compile errors of the shipped
shaders are therefore caught BEFORE release — the render tier and the frozen
self-test compile every one — not at run time.

No input handling and no orbit camera here: L1.3.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import (
    Q_ARG,
    Q_RETURN_ARG,
    QCoreApplication,
    QEvent,
    QEventLoop,
    QMetaObject,
    QObject,
    Qt,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPlatformSurfaceEvent,
    QQuaternion,
    QVector3D,
    QWindow,
)
from PyQt6.QtQuick import QQuickItem, QQuickView, QQuickWindow, QSGRendererInterface

from open_garden_planner.core.scene3d import (
    EnginePose,
    Rgba,
    engine_to_scene_points,
    scene_to_engine_points,
)

from .adapter import GroundPlacement, PartSpec
from .geometry import BufferGeometry
from .lifecycle import HostState, WindowFacts, state_of, why_no_frame
from .look import NEUTRAL_DAY, Look
from .materials import PART_COMPONENTS, tint_uniform
from .shots import PerspectiveShot, Shot, TopDownShot

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

QML_DIR: Final = Path(__file__).resolve().parent / "qml"
SCENE_FILE: Final = "Garden.qml"

_DIRECT: Final = Qt.ConnectionType.DirectConnection
_QUEUED: Final = Qt.ConnectionType.QueuedConnection
_LIGHT_FORWARD: Final = (0.0, 0.0, -1.0)  # a DirectionalLight shines down its local -Z
_LOG: Final = logging.getLogger(__name__)
_KEEP_WARNINGS: Final = 50  # QML warnings kept for health(); the rest are counted and logged
_WAIT_STEP_MS: Final = 5    # wait_ready(): how long one pass of its event loop may block

_API: Final = QSGRendererInterface.GraphicsApi
#: Our names for Qt's graphics APIs — keyed by the enum MEMBER, never by its
#: ``.name``: PyQt6 6.11 calls value 3 ``OpenGL`` but value 4 ``Direct3D11Rhi``
#: (the ``…Rhi`` aliases win or lose by declaration order). CI found it: the
#: Linux run reported ``OpenGL`` where a list written on Windows said ``OpenGLRhi``.
GRAPHICS_API_NAMES: Final = {
    _API.Unknown: "Unknown", _API.Software: "Software", _API.OpenVG: "OpenVG",
    _API.OpenGL: "OpenGL", _API.Direct3D11: "Direct3D11", _API.Vulkan: "Vulkan",
    _API.Metal: "Metal", _API.Null: "Null", _API.Direct3D12: "Direct3D12",
}
#: The APIs Qt Quick 3D draws with (RHI-based, and not the null backend).
RENDERING_APIS: Final = frozenset({"OpenGL", "Direct3D11", "Vulkan", "Metal", "Direct3D12"})

__all__ = [
    "GRAPHICS_API_NAMES", "QML_DIR", "RENDERING_APIS", "SCENE_FILE", "EngineError", "EnginePart",
    "HostHealth", "HostState", "HostStateError", "PickHit", "Quick3DHost", "shown_quick_windows",
]


class EngineError(RuntimeError):
    """The engine could not do what it was asked (QML did not load, a part was
    refused or could not be destroyed, a property is missing, a scene function
    failed, this backend cannot render 3D)."""


class HostStateError(RuntimeError):
    """A call the host cannot answer truthfully in its current state."""


@dataclass(frozen=True, slots=True)
class PickHit:
    """What is under a view position: the item, its mesh part and the hit point in
    the SCENE frame (east, north, up — centimetres)."""

    item_id: str
    part_index: int
    point: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class EnginePart:
    """One Model as the ENGINE holds it — read back from the QML scene, not from
    the sink's books (``Quick3DHost.engine_parts``)."""

    item_id: str
    part_index: int
    kind: str
    position: tuple[float, float, float]  # engine frame
    yaw_deg: float
    tint_rgb: tuple[int, int, int]
    casts_shadow: bool
    has_geometry: bool
    visible: bool


@dataclass(frozen=True, slots=True)
class HostHealth:
    """What Qt reported about a host on the GUI thread (``Quick3DHost.health``).

    Poll it — a timer in the workspace (L1.3) turns it into a notice. Everything
    in it was also written to the log when it arrived.
    """

    #: Frames Qt presented so far. 0 some time after ``wait_ready()`` returned
    #: True means nothing reaches the screen. (With the display asleep it stays
    #: at 1: never wait for a particular count.)
    frames_presented: int
    #: The QML engine's warnings since the host was built — a binding that
    #: failed, a scene function that threw. The first ones; ``qml_warning_count``
    #: counts all.
    qml_warnings: tuple[str, ...]
    qml_warning_count: int
    #: Qt could not create the graphics device for this window
    #: (``QQuickWindow.sceneGraphError``); the host will never become live.
    scene_graph_error: str | None

    @property
    def ok(self) -> bool:
        """No QML warning and no graphics failure (says nothing about frames)."""
        return self.scene_graph_error is None and self.qml_warning_count == 0


class _Part:
    """The handle of one engine Model: the Model and the geometry only it uses.

    The HOST owns both (``Quick3DHost._parts``); a sink holds handles as names.
    """

    __slots__ = ("geometry", "kind", "model")

    def __init__(self, model: QObject, geometry: BufferGeometry, kind: str) -> None:
        self.model: QObject | None = model
        self.geometry: BufferGeometry | None = geometry
        self.kind = kind

    def forget(self) -> None:
        self.model = None
        self.geometry = None


class _SurfaceWatch(QObject):
    """Latches "a platform window was created" from Qt's ``PlatformSurface`` event.

    ``QWindow.handle()`` is the same fact and PyQt6 does not bind it; ``winId()``
    would CREATE the window. The event is sent synchronously by
    ``QWindow::create()``, on the GUI thread.
    """

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.created = False

    def eventFilter(self, _watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt's name
        if (isinstance(event, QPlatformSurfaceEvent) and event.surfaceEventType()
                == QPlatformSurfaceEvent.SurfaceEventType.SurfaceCreated):
            self.created = True
        return False


def _connect_queued(signal: Any, slot: Callable[..., None]) -> None:
    """``signal.connect(slot, QueuedConnection)``: the slot runs in the event loop of the
    thread that connected — the GUI thread — wherever Qt emits the signal. (PyQt takes
    the connection type as a second argument; its type stubs do not declare it, hence
    the untyped ``signal``.)"""
    signal.connect(slot, _QUEUED)


def _js_object(value: Any) -> Any:
    """A QML function's JS return value arrives as ``QJSValue``: unwrap it."""
    return value.toVariant() if hasattr(value, "toVariant") else value


def _set(target: QObject, name: str, value: object) -> None:
    """Write a DECLARED property; a missing one is an error, not a silent dynamic
    property (a renamed QML property would otherwise be set into the void)."""
    meta = target.metaObject()
    if meta is None or meta.indexOfProperty(name) < 0:
        raise EngineError(f"the scene has no property {name!r} (renamed in {SCENE_FILE}?)")
    if not target.setProperty(name, value):
        raise EngineError(f"the scene refused {value!r} for its property {name!r}")


def _get(target: QObject, name: str) -> Any:
    meta = target.metaObject()
    if meta is None or meta.indexOfProperty(name) < 0:
        raise EngineError(f"the scene has no property {name!r} (renamed in {SCENE_FILE}?)")
    return target.property(name)


class Quick3DHost:
    """The garden's Qt Quick 3D view (module docstring). GUI thread only.

    ``size``: logical pixels of a standalone window. ``qml_dir``: where
    ``Garden.qml`` and its shaders are loaded from (tests point it at a copy).
    ``log``: an optional ``log(phase, **fields)`` callable.
    """

    def __init__(self, size: tuple[int, int] = (1280, 720), *, qml_dir: Path | None = None,
                 look: Look = NEUTRAL_DAY, log: Callable[..., None] | None = None) -> None:
        self._log: Callable[..., None] = log if log is not None else (lambda *_a, **_k: None)
        self._parts: set[_Part] = set()  # THE owner of every Model and geometry
        self._container: QWidget | None = None
        self._spent = False   # the one offscreen frame was taken (Qt cannot tell us)
        self._closed = False  # close() ran to its end (Qt deletes the view only later)
        self._qml_warnings: list[str] = []
        self._qml_warning_count = 0
        self._scene_graph_error: str | None = None
        self._gui_thread = threading.get_ident()
        #: Slot calls that arrived on another thread than the one that built the host.
        #: Must stay 0 — the render tier asserts it: no Python on Qt's render thread.
        self.slot_calls_off_the_gui_thread = 0
        self.frames_presented = 0
        self.grabs = 0
        started = time.perf_counter()
        self._view = QQuickView()
        self._surface = _SurfaceWatch(self._view)
        self._view.installEventFilter(self._surface)
        self._view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        # A hidden window keeps its scene graph (both are Qt's defaults; said out loud
        # because "scene graph up" must not flicker with visibility).
        self._view.setPersistentSceneGraph(True)
        self._view.setPersistentGraphics(True)
        self._view.resize(*size)
        # What Qt tells the GUI thread — connected BEFORE the scene loads. Queued where
        # Qt may emit from its render thread: no Python ever runs there (module docstring).
        engine = self._view.engine()
        if engine is not None:
            engine.warnings.connect(self._on_qml_warnings)
        _connect_queued(self._view.sceneGraphError, self._on_scene_graph_error)
        _connect_queued(self._view.frameSwapped, self._on_frame_swapped)
        source = (qml_dir if qml_dir is not None else QML_DIR) / SCENE_FILE
        self._view.setSource(QUrl.fromLocalFile(str(source)))
        root = self._view.rootObject()
        if root is None:
            errors = "; ".join(error.toString() for error in self._view.errors())
            self._view.deleteLater()
            self._closed = True
            raise EngineError(f"{source} did not load: {errors or 'no root object'}")
        self._root: QQuickItem = root
        self.qml_load_ms = (time.perf_counter() - started) * 1000.0
        self._log("qml_loaded", ms=round(self.qml_load_ms, 1), source=source.name)
        try:
            self.set_look(look)
        except Exception:
            self.close()  # a scene file without the look's properties: no half-built host
            raise

    # ── state ────────────────────────────────────────────────────────────────

    def _facts(self) -> WindowFacts | None:
        """What Qt says right now — the ONLY input of the state (None: the view is gone)."""
        view = self._view
        if sip.isdeleted(view):
            return None
        minimised = view.visibility() == QWindow.Visibility.Minimized
        container = self._container
        if not minimised and container is not None and not sip.isdeleted(container):
            top_level = container.window()
            minimised = top_level is not None and top_level.isMinimized()
        return WindowFacts(
            surface_created=self._surface.created, visible=bool(view.isVisible()),
            minimised=bool(minimised), width=int(view.width()), height=int(view.height()),
            scene_graph=bool(view.isSceneGraphInitialized()), exposed=bool(view.isExposed()),
            root_width=float(self._root.width()), root_height=float(self._root.height()))

    @property
    def state(self) -> HostState:
        """Derived from Qt on every call (module docstring; ``lifecycle.state_of``)."""
        return state_of(self._facts(), closed=self._closed, spent=self._spent)

    @property
    def is_closed(self) -> bool:
        """True after ``close()`` — and when Qt destroyed the view itself, which happens
        when the widget that parents ``container()`` is destroyed."""
        return self.state is HostState.CLOSED

    def is_visible(self) -> bool:
        facts = self._facts()
        return facts is not None and not self._closed and facts.visible

    def _look(self, call: str) -> tuple[HostState, WindowFacts]:
        """State and facts of an OPEN host — ``HostStateError`` when it is closed."""
        facts = self._facts()
        state = state_of(facts, closed=self._closed, spent=self._spent)
        if facts is None or state is HostState.CLOSED:
            raise HostStateError(f"{call}(): the host is closed (or Qt destroyed its view)")
        return state, facts

    def _open(self, call: str) -> HostState:
        return self._look(call)[0]

    # ── EngineScene (driven by Quick3DSink) ──────────────────────────────────

    def create_part(self, spec: PartSpec, pose: EnginePose, tint_rgba: Rgba) -> object:
        self._open("create_part")
        if spec.kind not in PART_COMPONENTS:
            raise EngineError(f"no part component for material kind {spec.kind!r}")
        geometry = BufferGeometry(spec.buffers)
        model = QMetaObject.invokeMethod(
            self._root, "createPart", _DIRECT, Q_RETURN_ARG("QVariant"),
            Q_ARG("QVariant", spec.kind), Q_ARG("QVariant", spec.item_id),
            Q_ARG("QVariant", spec.part_index), Q_ARG("QVariant", geometry),
            Q_ARG("QVariant", spec.casts_shadow))
        if not isinstance(model, QObject):
            raise EngineError(
                f"the scene created no Model for part {spec.part_index} ({spec.kind}) "
                f"of item {spec.item_id!r}")
        part = _Part(model, geometry, spec.kind)
        self._parts.add(part)
        try:
            self.pose_part(part, pose)
            self.tint_part(part, tint_rgba)
        except Exception:
            self._destroy(part)  # never a Model on screen that no caller holds a handle to
            raise
        return part

    def destroy_part(self, handle: object) -> None:
        """Destroy one Model. On a closed host: a no-op — it is gone already."""
        if self.state is HostState.CLOSED:
            if isinstance(handle, _Part):
                handle.forget()
            return
        self._destroy(self._part(handle))

    def part_count(self) -> int:
        """How many Models the engine holds for this host (its own books — which a failed
        destroy keeps in step with the engine: ``_destroy``)."""
        self._open("part_count")
        return len(self._parts)

    def clear(self) -> None:
        """Destroy EVERY Model, switch the sun off, drop the plan ground. On a closed
        host: a no-op. This is the sink's teardown (``Quick3DSink.clear``).

        It finishes whatever fails: every Model is tried, then the sun, then the
        ground, and only then the first failure is raised (``EngineError``). A Model
        the engine did not destroy STAYS in the host's books — books and engine agree
        after a failure, and the next ``clear()`` tries it again.
        """
        if self.state is HostState.CLOSED:
            self._forget_parts()
            return
        total = len(self._parts)
        failures: list[Exception] = []
        for part in list(self._parts):
            try:
                self._destroy(part)
            except Exception as error:  # noqa: BLE001 - finish the teardown, then report
                failures.append(error)
        steps: tuple[Callable[[], None], ...] = (
            lambda: self.set_sun(None), lambda: self.set_ground(None))
        for step in steps:
            try:
                step()
            except Exception as error:  # noqa: BLE001 - as above
                failures.append(error)
        if failures:
            raise EngineError(
                f"clear(): {len(failures)} of {total + 2} steps failed; the engine still holds "
                f"{len(self._parts)} Model(s), and so do the host's books. First: "
                f"{failures[0]}") from failures[0]

    def pose_part(self, handle: object, pose: EnginePose) -> None:
        self._open("pose_part")
        model = self._model(handle)
        _set(model, "position", QVector3D(*pose.position))
        _set(model, "eulerRotation", QVector3D(0.0, pose.yaw_deg, 0.0))

    def tint_part(self, handle: object, tint_rgba: Rgba) -> None:
        self._open("tint_part")
        model = self._model(handle)
        _set(model, "tint", QColor(tint_rgba[0], tint_rgba[1], tint_rgba[2]))
        _set(model, "tintLinear", QVector3D(*tint_uniform(tint_rgba)))

    def set_sun(self, travel: tuple[float, float, float] | None) -> None:
        self._open("set_sun")
        if travel is None:
            _set(self._root, "sunUp", False)
            return
        rotation = QQuaternion.rotationTo(QVector3D(*_LIGHT_FORWARD), QVector3D(*travel))
        _set(self._root, "sunRotation", rotation)
        _set(self._root, "sunUp", True)

    def set_ground(self, ground: GroundPlacement | None) -> None:
        self._open("set_ground")
        if ground is None:
            _set(self._root, "groundVisible", False)
            _set(self._root, "groundCenter", QVector3D(0.0, 0.0, 0.0))
            return
        _set(self._root, "groundCenter", QVector3D(*ground.center))
        _set(self._root, "groundWidth", float(ground.width_cm))
        _set(self._root, "groundDepth", float(ground.depth_cm))
        _set(self._root, "groundVisible", True)

    def request_frame(self) -> None:
        """A transaction closed. A live view is asked to repaint; any other open
        state shows the new scene with its next frame anyway."""
        if self._open("request_frame") is HostState.LIVE:
            self._view.update()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def container(self, parent: QWidget | None = None) -> QWidget:
        """The widget that embeds the view in a QWidget layout (created once).

        It wraps a NATIVE window: sibling widgets do not paint over it. After
        this call ``show()`` / ``hide()`` act on the container, the parent may
        show, hide, resize and collapse it as it likes — the state follows Qt —
        and the host has no offscreen frame any more. Legal in every open state
        but ``SPENT``: a host whose scene is gone would die when its parent
        shows it (measured: embedding a view that is live, starting or hidden is
        fine).
        """
        state = self._open("container")
        if self._container is None:
            if state is HostState.SPENT:
                raise HostStateError(
                    "container(): this host took its one offscreen frame and its scene is "
                    "gone; embedded, it would die when its parent shows it. Build a new host")
            from PyQt6.QtWidgets import QWidget

            self._container = QWidget.createWindowContainer(self._view, parent)
        return self._container

    def show(self) -> None:
        state = self._open("show")
        if state is HostState.SPENT:
            raise HostStateError(
                "show(): this host took its one offscreen frame and its scene is gone; "
                "showing it now would kill the process. Build a new host for a window")
        if self._container is not None:
            self._container.show()
        else:
            self._view.show()

    def hide(self) -> None:
        self._open("hide")
        if self._container is not None:
            self._container.hide()
        else:
            self._view.hide()

    def wait_ready(self, timeout_s: float = 10.0) -> bool:
        """Run the event loop until the host is ``LIVE``; True then — and only then is a
        ``grab()`` issued right after it truthful.

        It waits while the host is ``STARTING`` (shown, on its way: the first expose
        after ``show()``, the next one after a re-show, the root item after a resize
        or a reopened pane — one pass each, on Windows). It returns False without
        waiting when nothing is on its way — the host is new, hidden, minimised,
        collapsed, spent, or Qt reported that the graphics device could not be
        created — and False when ``timeout_s`` is up, when the host is closed or
        its window is destroyed or hidden meanwhile. The state is looked at again
        in every pass, and a pass BLOCKS for events (a few milliseconds at most):
        no busy loop. It runs the event loop; call it where re-entrancy is
        acceptable.

        A visible host is first given what is ALREADY pending in the event loop:
        a pane that was re-shown in a splitter a moment ago still has its old,
        empty geometry until the layout request is delivered — it reads
        ``HIDDEN`` (no area), and one delivery later it is on its way.
        """
        self._open("wait_ready")
        started = time.perf_counter()
        deadline = started + timeout_s
        if self.is_visible() and self.state is not HostState.LIVE:
            QCoreApplication.processEvents()  # what is pending, without waiting
        while (self.state is HostState.STARTING and self._scene_graph_error is None
               and time.perf_counter() < deadline):
            self._run_events(_WAIT_STEP_MS)
        ready = self.state is HostState.LIVE
        self._log("ready", ok=ready, state=self.state.value,
                  ms=round((time.perf_counter() - started) * 1000.0, 1))
        return ready

    def close(self) -> None:
        """Destroy every Model, then the view (and its container). The host is dead after.

        Safe when Qt got there first (the container's parent widget was destroyed, and
        the window with it): then only the Python side is released. Exception-safe
        and retryable: the window goes whatever a Model's teardown said (a Model that
        could not be destroyed dies with the view; its geometry stays referenced
        until then), and the host counts as closed only once hiding and deleting the
        window went through — a ``close()`` that raised can simply be called again.
        """
        if self._closed:
            return
        if sip.isdeleted(self._view):
            self._forget_parts()  # the Models died with the scene
            self._container = None
            self._closed = True
            return
        for part in list(self._parts):
            try:
                self._destroy(part)
            except Exception as error:  # noqa: BLE001 - the Model dies with the view
                _LOG.warning("close(): a Model could not be destroyed (%s); it goes with the view",
                             error)
        self._view.hide()
        self._view.removeEventFilter(self._surface)
        container, self._container = self._container, None
        if container is not None and not sip.isdeleted(container):
            container.deleteLater()  # owns the window
        else:
            self._view.deleteLater()
        self._closed = True

    # ── frames ───────────────────────────────────────────────────────────────

    def graphics_api(self) -> str:
        """``Direct3D11``, ``OpenGL``, ``Vulkan``, ``Metal``, ``Direct3D12`` — or
        ``Software`` / ``Null`` / ``OpenVG`` / ``Unknown`` (``GRAPHICS_API_NAMES``).
        Known before the first frame: it is the API the window WILL use."""
        self._open("graphics_api")
        interface = self._view.rendererInterface()
        if interface is None:
            return "Unknown"
        return GRAPHICS_API_NAMES.get(interface.graphicsApi(), "Unknown")

    def renders_3d(self) -> bool:
        """False where Qt Quick 3D draws nothing — the software scene graph (what the
        ``offscreen`` platform selects), the null backend. A frame from such a host
        would show the scene's 2D items and no garden."""
        return self.graphics_api() in RENDERING_APIS

    def grab(self, label: str = "") -> QImage:
        """Render the current state NOW and read it back (device pixels).

        Legal on a ``LIVE`` host, and ONCE on a ``NEW`` host that is not embedded
        (the offscreen frame; the host is ``SPENT`` after). Every other state
        raises ``HostStateError`` — in ``STARTING`` because a grab would kill the
        process or show the old size, elsewhere because there is nothing to show.
        ``EngineError`` when this backend cannot render 3D at all, and when the
        image that comes back is not the window's size.
        """
        state, facts = self._look("grab")
        self._require_3d("grab")
        if state is HostState.NEW and self._container is None:
            self._spent = True  # before the grab: whatever happens, never a second one
            self._view.create()
        elif state is not HostState.LIVE:
            raise HostStateError(
                f"grab(): {why_no_frame(state, facts, embedded=self._container is not None)}")
        started = time.perf_counter()
        image = self._view.grabWindow()
        elapsed = (time.perf_counter() - started) * 1000.0
        self._log("grab", label=label, ms=round(elapsed, 1), state=state.value,
                  px=f"{image.width()}x{image.height()}")
        if image.isNull() or image.width() == 0 or image.height() == 0:
            raise EngineError(f"grab({label!r}) returned no image")
        ratio = float(image.devicePixelRatio())
        if (abs(image.width() - facts.width * ratio) > 2.0
                or abs(image.height() - facts.height * ratio) > 2.0):
            raise EngineError(
                f"grab({label!r}) returned a {image.width()} x {image.height()} px image for a "
                f"{facts.width} x {facts.height} window at ratio {ratio:g}: not the current scene")
        self.grabs += 1
        return image

    def device_pixel_ratio(self) -> float:
        self._open("device_pixel_ratio")
        return float(self._view.effectiveDevicePixelRatio())

    def view_size(self) -> tuple[float, float]:
        """The view's actual LOGICAL size (a window larger than the screen is clamped)."""
        self._open("view_size")
        return float(self._root.width()), float(self._root.height())

    # ── camera and look ──────────────────────────────────────────────────────

    def set_shot(self, shot: Shot) -> None:
        if isinstance(shot, TopDownShot):
            self.set_top_down(shot.center, shot.px_per_cm)
        else:
            self.set_camera(shot.eye, shot.target, shot.fov_deg)

    def set_camera(self, eye_scene: tuple[float, float, float],
                   target_scene: tuple[float, float, float], fov_deg: float = 40.0) -> None:
        """Perspective: the eye and the point it looks at, scene frame (E, N, up)."""
        self._open("set_camera")
        shot = PerspectiveShot(eye_scene, target_scene, fov_deg)  # validates
        _set(self._root, "topDown", False)
        _set(self._root, "camPos", _engine_vector(shot.eye))
        _set(self._root, "camTarget", _engine_vector(shot.target))
        _set(self._root, "camFov", float(shot.fov_deg))
        _set(self._root, "camVersion", int(_get(self._root, "camVersion")) + 1)

    def set_top_down(self, center_scene: tuple[float, float], px_per_cm: float) -> None:
        """Orthographic, straight down, north up: the truth view (``shots.TopDownShot``)."""
        self._open("set_top_down")
        shot = TopDownShot(center_scene, px_per_cm)  # validates
        _set(self._root, "camTarget", _engine_vector((*shot.center, 0.0)))
        _set(self._root, "topDownScale", float(shot.px_per_cm))
        _set(self._root, "topDown", True)

    def set_look(self, look: Look) -> None:
        """Apply a mood — plain data (``look.Look``); L1.4 passes its own."""
        self._open("set_look")
        _set(self._root, "sunColor", QColor(look.sun_color))
        _set(self._root, "sunBrightness", float(look.sun_brightness))
        _set(self._root, "fillColor", QColor(look.fill_color))
        _set(self._root, "exposure", float(look.exposure))
        _set(self._root, "skyColor", QColor(look.sky_color))
        _set(self._root, "meadowColor", QColor(look.meadow_albedo))
        _set(self._root, "windStrength", float(look.wind_strength))

    # ── picking and readback ─────────────────────────────────────────────────

    def pick(self, x: float, y: float) -> PickHit | None:
        """The item under view position ``(x, y)`` (LOGICAL pixels), or None for
        empty space — for the frame rendered last.

        ``LIVE`` hosts only; anything else raises ``HostStateError`` (a backend
        that renders no 3D, or a scene whose pick function failed,
        ``EngineError``): "no hit" must never mean "could not look".
        """
        self._require_live("pick")
        result = self._call("pickAt", float(x), float(y))
        if not result.get("hit"):
            return None
        point = engine_to_scene_points(
            np.array([result["x"], result["y"], result["z"]], dtype=np.float64))
        return PickHit(item_id=str(result.get("id", "")), part_index=int(result.get("part", -1)),
                       point=(float(point[0]), float(point[1]), float(point[2])))

    def project(self, east_cm: float, north_cm: float, up_cm: float) -> tuple[float, float]:
        """Scene point → view position in LOGICAL pixels (the space ``pick`` takes),
        by the ENGINE's projection of the frame rendered last. ``LIVE`` hosts only."""
        self._require_live("project")
        engine = _engine_vector((east_cm, north_cm, up_cm))
        result = self._call("projectToView", engine.x(), engine.y(), engine.z())
        return float(result["x"]), float(result["y"])

    def engine_parts(self) -> list[EnginePart]:
        """Every Model of the QML scene that was not destroyed — what the engine holds,
        read from it (a Model a later story hides is still listed, ``visible=False``)."""
        self._open("engine_parts")
        rows = self._call("listParts")
        return [EnginePart(
            item_id=str(row["id"]), part_index=int(row["part"]), kind=str(row["kind"]),
            position=(float(row["x"]), float(row["y"]), float(row["z"])),
            yaw_deg=float(row["yaw"]),
            tint_rgb=(round(row["r"] * 255.0), round(row["g"] * 255.0), round(row["b"] * 255.0)),
            casts_shadow=bool(row["casts"]), has_geometry=bool(row["geometry"]),
            visible=bool(row["visible"]),
        ) for row in rows]

    def scene_property(self, name: str) -> Any:
        """A root property of ``Garden.qml`` (tests and diagnostics); ``EngineError``
        for a name the scene does not declare."""
        self._open("scene_property")
        return _get(self._root, name)

    # ── trouble in a live view (GUI thread; module docstring) ────────────────

    def health(self) -> HostHealth:
        """What Qt reported so far: frames presented, QML warnings, a graphics device
        that could not be created. Answers in every state, also on a closed host."""
        return HostHealth(self.frames_presented, tuple(self._qml_warnings),
                          self._qml_warning_count, self._scene_graph_error)

    # ── internals ────────────────────────────────────────────────────────────

    def _on_gui_thread(self) -> None:
        if threading.get_ident() != self._gui_thread:
            self.slot_calls_off_the_gui_thread += 1

    def _on_frame_swapped(self) -> None:
        self._on_gui_thread()
        self.frames_presented += 1

    def _on_qml_warnings(self, warnings: Any) -> None:
        self._on_gui_thread()
        for warning in warnings:
            text = str(warning.toString())
            self._qml_warning_count += 1
            if len(self._qml_warnings) < _KEEP_WARNINGS:
                self._qml_warnings.append(text)
                _LOG.warning("3D view: QML warning: %s", text)
        self._log("qml_warnings", count=self._qml_warning_count)

    def _on_scene_graph_error(self, _error: Any, message: str) -> None:
        self._on_gui_thread()
        self._scene_graph_error = str(message) or "the graphics device could not be created"
        _LOG.error("3D view: the graphics device could not be created: %s",
                   self._scene_graph_error)
        self._log("scene_graph_error", message=self._scene_graph_error)

    @staticmethod
    def _run_events(milliseconds: int) -> None:
        """One pass of a local event loop that BLOCKS for events — never a busy spin."""
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def _call(self, function: str, *arguments: object) -> Any:
        """Call a function of ``Garden.qml`` that returns an object. A function that is
        missing raises in PyQt; one that THROWS returns nothing, silently (the QML
        engine only warns) — both are an ``EngineError`` here, never an empty answer."""
        try:
            result = _js_object(QMetaObject.invokeMethod(
                self._root, function, _DIRECT, Q_RETURN_ARG("QVariant"),
                *(Q_ARG("QVariant", argument) for argument in arguments)))
        except RuntimeError as error:
            raise EngineError(f"the scene has no function {function}() ({error})") from error
        if result is None:
            raise EngineError(f"the scene's {function}() returned nothing — it failed "
                              f"(see health().qml_warnings)")
        return result

    def _require_3d(self, call: str) -> None:
        api = self.graphics_api()
        if api not in RENDERING_APIS:
            raise EngineError(
                f"{call}(): Qt Quick runs on the {api} backend here, where Qt Quick 3D draws "
                "nothing (QT_QPA_PLATFORM=offscreen selects the software scene graph)")

    def _require_live(self, call: str) -> None:
        state, facts = self._look(call)
        self._require_3d(call)
        if state is not HostState.LIVE:
            raise HostStateError(
                f"{call}(): {why_no_frame(state, facts, embedded=self._container is not None)}")

    def _destroy(self, part: _Part) -> None:
        """Destroy one Model in the engine. The part leaves the host's books ONLY when the
        engine confirms it — the Model is retired (or already deleted). After a failure
        books and engine still agree: both hold it, and the caller may try again."""
        model = part.model
        if model is None or sip.isdeleted(model):
            self._parts.discard(part)
            part.forget()
            return
        # The Model lets go of the geometry before either dies: the geometry is
        # owned by Python, and a Model must never outlive the buffers it draws.
        try:
            QMetaObject.invokeMethod(self._root, "destroyPart", _DIRECT, Q_ARG("QVariant", model))
        except RuntimeError as error:
            raise EngineError(f"the scene has no function destroyPart() ({error})") from error
        if not sip.isdeleted(model) and model.property("retired") is not True:
            raise EngineError("the scene's destroyPart() did not retire the Model — it failed "
                              "(see health().qml_warnings); the engine still holds it")
        self._parts.discard(part)
        part.forget()

    def _forget_parts(self) -> None:
        for part in self._parts:
            part.forget()
        self._parts.clear()

    def _part(self, handle: object) -> _Part:
        if not isinstance(handle, _Part) or handle.model is None or handle not in self._parts:
            raise EngineError(f"not a live part of this host: {handle!r}")
        return handle

    def _model(self, handle: object) -> QObject:
        model = self._part(handle).model
        if model is None:  # _part() checked it; this narrows the type
            raise EngineError(f"not a live part of this host: {handle!r}")
        return model


def shown_quick_windows() -> int:
    """How many Qt Quick windows are visible in this process right now. Each renders on
    a thread of its own (the threaded render loop), beside which no process-wide Qt
    message recorder may be installed (``qt_messages.py``)."""
    return sum(1 for window in QGuiApplication.allWindows()
               if isinstance(window, QQuickWindow) and window.isVisible())


def _engine_vector(point_scene: tuple[float, float, float]) -> QVector3D:
    engine = scene_to_engine_points(np.array(point_scene, dtype=np.float64))
    return QVector3D(float(engine[0]), float(engine[1]), float(engine[2]))
