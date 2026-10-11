"""``Quick3DHost`` — the ``QQuickView`` that shows the garden (L1.2). ENGINE module.

One object owns the Qt Quick window, the loaded ``qml/Garden.qml`` and every
engine ``Model``. It is the ``EngineScene`` a ``Quick3DSink`` drives, and it is
what the workspace embeds (L1.3: ``container()``) and what a render reads back
(``grab()``).

Host choice (ADR-048 criterion 3): a ``QQuickView`` in a
``QWidget.createWindowContainer`` — never a ``QQuickWidget``, which moves the
whole top-level window onto RHI composition. Create it once and keep it: hide
and show it, never rebuild it per open.

THE STATE MACHINE (ADR-048 addendum L1.2, fix round 1). A Qt Quick window can be
asked for a frame in situations where answering kills the process, so the host
never guesses: ``state`` is derived from Qt's own predicates on every call, and
every public method either answers truthfully or raises.

==========  ===========================================================  =====================
state       what Qt says                                                 a frame?
==========  ===========================================================  =====================
``NEW``     no scene graph, not visible, the offscreen frame not taken   ONE, offscreen
``STARTING``  visible, no scene graph yet (shown, first expose pending)  no — grabbing kills
``LIVE``    scene graph up, visible and exposed                          yes: ``grab``, ``pick``
``HIDDEN``  scene graph up, not both (hidden, minimised, just re-shown)  no (refused)
``SPENT``   took its one offscreen frame                                 never again
``CLOSED``  ``close()`` was called, or Qt destroyed the view             —
==========  ===========================================================  =====================

Measured, Qt 6.11, Direct3D 11 on an RTX 4070 and on WARP:

- ``grabWindow()`` on a window WITHOUT a scene graph renders one frame offscreen
  and tears everything down. For a window that was never shown that is the one
  offscreen frame (correct, pixel-identical to a shown grab); a second one is
  the bare background. For a window that IS shown and waits for its first
  expose — or is shown afterwards — the process dies in the next event-loop
  pass (abort, access violation or heap corruption; 5 of 5). Hence ``STARTING``
  refuses to grab, ``SPENT`` refuses ``show()`` and ``container()``, and an
  embedded host has no offscreen frame at all (its parent may show it any time).
- One ``processEvents()`` after ``show()`` brings the scene graph up
  (``wait_ready`` pumps until then, bounded).
- A grab while ``HIDDEN`` returned a correct frame on Direct3D 11. It is refused
  all the same: unmeasured on OpenGL, and no caller needs it.
- Under the SOFTWARE scene graph (``QT_QPA_PLATFORM=offscreen``) Qt Quick 3D
  draws nothing and reports a scene graph from birth. ``renders_3d()`` is False
  there, and ``grab`` / ``pick`` / ``project`` raise ``EngineError``: a frame
  without the garden must not pass for one.

Do not wait for ``frameSwapped`` to be sure of a frame: on Windows, update
requests come from a display-vblank thread, and with the monitor asleep a live
window presents its first frame and then none. ``grab()`` on a ``LIVE`` host
renders the current state synchronously — it is the synchronisation point.

A closed host: ``destroy_part``, ``clear`` and ``close`` are no-ops (they release
what is gone already), ``state`` / ``is_closed`` / ``is_visible`` answer, and
every other call raises ``HostStateError``.

Errors in a LIVE view (a shader that does not compile is only a Qt warning, and
a frozen exe has no stderr): ``watch_messages()`` records every Qt message until
``close()``, writes warnings and errors to the app's log, and ``engine_errors()``
tells the owner on the GUI thread. L1.3 turns that into a notice.

No input handling and no orbit camera here: L1.3.
"""

from __future__ import annotations

import enum
import logging
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
    QEventLoop,
    QMetaObject,
    QObject,
    Qt,
    QUrl,
)
from PyQt6.QtGui import QColor, QImage, QQuaternion, QVector3D
from PyQt6.QtQuick import QQuickItem, QQuickView, QSGRendererInterface

from open_garden_planner.core.scene3d import (
    EnginePose,
    Rgba,
    engine_to_scene_points,
    scene_to_engine_points,
)

from . import qt_messages
from .adapter import GroundPlacement, PartSpec
from .geometry import BufferGeometry
from .look import NEUTRAL_DAY, Look
from .materials import PART_COMPONENTS, tint_uniform
from .shots import PerspectiveShot, Shot, TopDownShot

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

QML_DIR: Final = Path(__file__).resolve().parent / "qml"
SCENE_FILE: Final = "Garden.qml"

_DIRECT: Final = Qt.ConnectionType.DirectConnection
_LIGHT_FORWARD: Final = (0.0, 0.0, -1.0)  # a DirectionalLight shines down its local -Z
_LOG: Final = logging.getLogger(__name__)

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


class EngineError(RuntimeError):
    """The engine could not do what it was asked (QML did not load, a part was
    refused, a property is missing, this backend cannot render 3D)."""


class HostStateError(RuntimeError):
    """A call the host cannot answer truthfully in its current state."""


class HostState(enum.Enum):
    """Where a host is in its life (module docstring: the table and the measurements)."""

    NEW = "new"
    STARTING = "starting"
    LIVE = "live"
    HIDDEN = "hidden"
    SPENT = "spent"
    CLOSED = "closed"


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
        self._closed = False  # close() was called (Qt deletes the view only later)
        self._watch: qt_messages.QtMessages | None = None
        self._watch_previous: Any = None
        self.frames_presented = 0
        self.grabs = 0
        started = time.perf_counter()
        self._view = QQuickView()
        self._view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        # The state machine reads "has a scene graph" as "was live": keep Qt from
        # dropping the scene graph of a hidden window (both are Qt's defaults).
        self._view.setPersistentSceneGraph(True)
        self._view.setPersistentGraphics(True)
        self._view.resize(*size)
        source = (qml_dir if qml_dir is not None else QML_DIR) / SCENE_FILE
        self._view.setSource(QUrl.fromLocalFile(str(source)))
        root = self._view.rootObject()
        if root is None:
            errors = "; ".join(error.toString() for error in self._view.errors())
            self._view.deleteLater()
            self._closed = True
            raise EngineError(f"{source} did not load: {errors or 'no root object'}")
        self._root: QQuickItem = root
        self._view.frameSwapped.connect(self._on_frame_swapped)
        self.qml_load_ms = (time.perf_counter() - started) * 1000.0
        self._log("qml_loaded", ms=round(self.qml_load_ms, 1), source=source.name)
        try:
            self.set_look(look)
        except Exception:
            self.close()  # a scene file without the look's properties: no half-built host
            raise

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def state(self) -> HostState:
        """Derived from Qt on every call (module docstring)."""
        if self._closed or sip.isdeleted(self._view):
            return HostState.CLOSED
        if self._spent:
            return HostState.SPENT
        visible = self._view.isVisible()
        if self._view.isSceneGraphInitialized():
            # both: right after hide() Qt still calls the window exposed until the event
            # loop ran, and a minimised window stays "visible"
            return HostState.LIVE if visible and self._view.isExposed() else HostState.HIDDEN
        return HostState.STARTING if visible else HostState.NEW

    @property
    def is_closed(self) -> bool:
        """True after ``close()`` — and when Qt destroyed the view itself, which happens
        when the widget that parents ``container()`` is destroyed."""
        return self.state is HostState.CLOSED

    def is_visible(self) -> bool:
        return self.state is not HostState.CLOSED and bool(self._view.isVisible())

    def _open(self, call: str) -> HostState:
        """The state — or ``HostStateError`` when the host is closed."""
        state = self.state
        if state is HostState.CLOSED:
            raise HostStateError(f"{call}(): the host is closed (or Qt destroyed its view)")
        return state

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

    def clear(self) -> None:
        """Destroy EVERY Model, switch the sun off, drop the plan ground. On a closed
        host: a no-op. This is the sink's teardown (``Quick3DSink.clear``)."""
        if self.state is HostState.CLOSED:
            self._forget_parts()
            return
        for part in list(self._parts):
            self._destroy(part)
        self.set_sun(None)
        self.set_ground(None)

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
        show and hide it as it likes, and the host has no offscreen frame any
        more. Legal in every open state but ``SPENT`` — a host whose scene is gone
        would die when its parent shows it (measured: embedding a view that is
        already shown, starting or hidden is fine).
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
        """Pump events until the host is ``LIVE`` (exposed, scene graph up); True then.

        After ``show()`` the window has no scene graph until its first expose is
        delivered — one pass of the event loop on Windows — and ``grab()`` refuses
        until then. Returns False at once when nothing is on its way (the host is
        not visible, or spent), and False on timeout: a finding to act on, never a
        hang. It runs the event loop; call it where re-entrancy is acceptable.
        """
        state = self._open("wait_ready")
        started = time.perf_counter()
        if state is not HostState.LIVE and state is not HostState.SPENT and self._view.isVisible():
            deadline = started + timeout_s
            while self.state not in (HostState.LIVE, HostState.CLOSED):
                if time.perf_counter() >= deadline:
                    break
                QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        ready = self.state is HostState.LIVE
        self._log("ready", ok=ready, state=self.state.value,
                  ms=round((time.perf_counter() - started) * 1000.0, 1))
        return ready

    def close(self) -> None:
        """Destroy every Model, then the view (and its container). The host is dead after.

        Safe when Qt got there first (the container's parent widget was destroyed, and
        the window with it): then only the Python side is released.
        """
        if self._closed:
            return
        dead = sip.isdeleted(self._view)
        self._stop_watch()
        self._closed = True
        if dead:
            self._forget_parts()  # the Models died with the scene
            self._container = None
            return
        for part in list(self._parts):
            self._destroy(part)
        self._view.hide()
        if self._container is not None:
            self._container.deleteLater()  # owns the window
            self._container = None
        else:
            self._view.deleteLater()

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
        process, elsewhere because the answer would not be the current scene.
        ``EngineError`` when this backend cannot render 3D at all.
        """
        state = self._open("grab")
        self._require_3d("grab")
        if state is HostState.NEW and self._container is None:
            self._spent = True  # before the grab: whatever happens, never a second one
            self._view.create()
        elif state is not HostState.LIVE:
            raise HostStateError(f"grab(): {self._why_no_frame(state)}")
        started = time.perf_counter()
        image = self._view.grabWindow()
        elapsed = (time.perf_counter() - started) * 1000.0
        self._log("grab", label=label, ms=round(elapsed, 1), state=state.value,
                  px=f"{image.width()}x{image.height()}")
        if image.isNull() or image.width() == 0 or image.height() == 0:
            raise EngineError(f"grab({label!r}) returned no image")
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

        ``LIVE`` hosts only; anything else raises ``HostStateError`` (and a backend
        that renders no 3D ``EngineError``): "no hit" must never mean "could not look".
        """
        self._require_live("pick")
        result = _js_object(QMetaObject.invokeMethod(
            self._root, "pickAt", _DIRECT, Q_RETURN_ARG("QVariant"),
            Q_ARG("QVariant", float(x)), Q_ARG("QVariant", float(y))))
        if not result or not result.get("hit"):
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
        result = _js_object(QMetaObject.invokeMethod(
            self._root, "projectToView", _DIRECT, Q_RETURN_ARG("QVariant"),
            Q_ARG("QVariant", engine.x()), Q_ARG("QVariant", engine.y()),
            Q_ARG("QVariant", engine.z())))
        return float(result["x"]), float(result["y"])

    def engine_parts(self) -> list[EnginePart]:
        """Every Model of the QML scene that was not destroyed — what the engine holds,
        read from it (a Model a later story hides is still listed, ``visible=False``)."""
        self._open("engine_parts")
        rows = _js_object(QMetaObject.invokeMethod(
            self._root, "listParts", _DIRECT, Q_RETURN_ARG("QVariant"))) or []
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

    # ── errors in a live view ────────────────────────────────────────────────

    def watch_messages(self, logger: logging.Logger | None = None) -> qt_messages.QtMessages:
        """Listen to every Qt message from now until ``close()`` (idempotent).

        ``offscreen.render_records`` raises on a shader or QML error; a LIVE view
        has nobody to raise to — a shader that does not compile is a Qt warning,
        the frame still renders, in the wrong colours, and a frozen exe has no
        stderr. With a watch, warnings and errors go to ``logger`` (default: this
        module's, hence the app's ``app.log`` once ``core.logging_config`` is set
        up) and are counted; poll ``engine_errors()`` on the GUI thread.

        The recorder is called from Qt's render thread too: it only takes a lock
        and logs (both thread-safe), and never raises. Handlers chain — a recorder
        installed later and removed before ``close()`` (an offscreen render)
        nests cleanly; remove in reverse order of installation.
        """
        self._open("watch_messages")
        if self._watch is None:
            self._watch = qt_messages.QtMessages(
                qt_messages.logging_sink(logger if logger is not None else _LOG))
            self._watch_previous = qt_messages.install(self._watch)
        return self._watch

    def engine_errors(self) -> list[str]:
        """The shader / QML errors recorded since ``watch_messages()`` — empty means
        "none was reported", so it raises ``HostStateError`` when nobody listened."""
        if self._watch is None:
            raise HostStateError("engine_errors(): call watch_messages() first — without a "
                                 "watch nothing was recorded, which is not the same as no error")
        return self._watch.errors

    # ── internals ────────────────────────────────────────────────────────────

    def _on_frame_swapped(self) -> None:
        self.frames_presented += 1

    def _stop_watch(self) -> None:
        if self._watch is not None:
            qt_messages.uninstall(self._watch_previous)
            self._watch_previous = None
            # the recorder stays readable: engine_errors() after close() still answers

    def _require_3d(self, call: str) -> None:
        api = self.graphics_api()
        if api not in RENDERING_APIS:
            raise EngineError(
                f"{call}(): Qt Quick runs on the {api} backend here, where Qt Quick 3D draws "
                "nothing (QT_QPA_PLATFORM=offscreen selects the software scene graph)")

    def _require_live(self, call: str) -> None:
        state = self._open(call)
        self._require_3d(call)
        if state is not HostState.LIVE:
            raise HostStateError(f"{call}(): {self._why_no_frame(state)}")

    def _why_no_frame(self, state: HostState) -> str:
        if state is HostState.STARTING:
            return ("the window is shown but not exposed yet — it has no scene graph, and a "
                    "frame taken now would kill the process. Call wait_ready() first")
        if state is HostState.HIDDEN:
            return ("the host is not exposed (hidden, minimised, or shown again a moment "
                    "ago): make it visible and call wait_ready()")
        if state is HostState.SPENT:
            return ("this host took its one offscreen frame and its scene is gone. "
                    "Build a new host (offscreen.render_records does, per frame)")
        if state is HostState.NEW:
            return ("an embedded host has no offscreen frame (its parent may show it at any "
                    "time): show its container and wait_ready()" if self._container is not None
                    else "the host was never shown: show() it and wait_ready()")
        return f"the host is {state.value}"

    def _destroy(self, part: _Part) -> None:
        self._parts.discard(part)
        # The Model lets go of the geometry before either dies: the geometry is
        # owned by Python, and a Model must never outlive the buffers it draws.
        QMetaObject.invokeMethod(self._root, "destroyPart", _DIRECT, Q_ARG("QVariant", part.model))
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


def _engine_vector(point_scene: tuple[float, float, float]) -> QVector3D:
    engine = scene_to_engine_points(np.array(point_scene, dtype=np.float64))
    return QVector3D(float(engine[0]), float(engine[1]), float(engine[2]))
