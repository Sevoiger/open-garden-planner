"""``Quick3DHost`` — the ``QQuickView`` that shows the garden (L1.2). ENGINE module.

One object owns the Qt Quick window, the loaded ``qml/Garden.qml`` and every
engine ``Model``. It is the ``EngineScene`` a ``Quick3DSink`` drives, and it is
what the workspace embeds (L1.3: ``container()``) and what a render reads back
(``grab()``).

Host choice (ADR-048 criterion 3): a ``QQuickView`` in a
``QWidget.createWindowContainer`` — never a ``QQuickWidget``, which moves the
whole top-level window onto RHI composition (2D pan x1.4 on llvmpipe, x5.7 on
WARP). Create it once and keep it: hide and show it, never rebuild it per open
— a second host is not faster, and re-entry of a kept one costs milliseconds.

Frames and readback — measured, Qt 6.11 (ADR-048 addendum L1.2):

- ``grab()`` renders the CURRENT state synchronously and reads it back. It is
  the one point where "the engine shows what it was told" is certain. Do not
  wait for ``frameSwapped`` to be sure of a frame: on Windows, update requests
  are delivered from a display-vblank thread, and with the monitor asleep a
  visible, exposed window presents its first frame and then none.
- A host that was NEVER shown can ``grab()`` exactly once (Qt renders one frame
  offscreen and tears the scene graph down; a second such grab returns the bare
  background — every Model gone). ``offscreen.py`` builds one host per frame for
  that reason; here a second hidden grab raises instead of lying.
- ``pick()`` answers for the frame that was rendered last, so it needs a shown
  host and at least one frame.

No input handling and no orbit camera here: L1.3.
"""

from __future__ import annotations

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
from PyQt6.QtQuick import QQuickItem, QQuickView

from open_garden_planner.core.scene3d import (
    EnginePose,
    Rgba,
    engine_to_scene_points,
    scene_to_engine_points,
)

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


class EngineError(RuntimeError):
    """The engine could not do what it was asked (QML did not load, a part was refused)."""


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


class _Part:
    """The handle of one engine Model: the Model and the geometry only it uses."""

    __slots__ = ("geometry", "kind", "model")

    def __init__(self, model: QObject, geometry: BufferGeometry, kind: str) -> None:
        self.model: QObject | None = model
        self.geometry: BufferGeometry | None = geometry
        self.kind = kind


def _js_object(value: Any) -> Any:
    """A QML function's JS return value arrives as ``QJSValue``: unwrap it."""
    return value.toVariant() if hasattr(value, "toVariant") else value


class Quick3DHost:
    """The garden's Qt Quick 3D view (module docstring). GUI thread only.

    ``size``: logical pixels of a standalone window. ``qml_dir``: where
    ``Garden.qml`` and its shaders are loaded from (tests point it at a copy).
    ``log``: an optional ``log(phase, **fields)`` callable.
    """

    def __init__(self, size: tuple[int, int] = (1280, 720), *, qml_dir: Path | None = None,
                 look: Look = NEUTRAL_DAY, log: Callable[..., None] | None = None) -> None:
        self._log: Callable[..., None] = log if log is not None else (lambda *_a, **_k: None)
        self._parts: list[_Part] = []
        self._container: QWidget | None = None
        self._ever_shown = False
        self._hidden_grab_done = False
        self._closed = False
        self.frames_presented = 0
        self.grabs = 0
        started = time.perf_counter()
        self._view = QQuickView()
        self._view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self._view.resize(*size)
        source = (qml_dir if qml_dir is not None else QML_DIR) / SCENE_FILE
        self._view.setSource(QUrl.fromLocalFile(str(source)))
        root = self._view.rootObject()
        if root is None:
            errors = "; ".join(error.toString() for error in self._view.errors())
            self._view.deleteLater()
            raise EngineError(f"{source} did not load: {errors or 'no root object'}")
        self._root: QQuickItem = root
        self._view.frameSwapped.connect(self._on_frame_swapped)
        self.qml_load_ms = (time.perf_counter() - started) * 1000.0
        self._log("qml_loaded", ms=round(self.qml_load_ms, 1), source=source.name)
        self.set_look(look)

    # ── EngineScene (driven by Quick3DSink) ──────────────────────────────────

    def create_part(self, spec: PartSpec, pose: EnginePose, tint_rgba: Rgba) -> object:
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
        self._parts.append(part)
        self.pose_part(part, pose)
        self.tint_part(part, tint_rgba)
        return part

    def destroy_part(self, handle: object) -> None:
        part = self._part(handle)
        self._parts.remove(part)
        # The Model lets go of the geometry before either dies: the geometry is
        # owned by Python, and a Model must never outlive the buffers it draws.
        QMetaObject.invokeMethod(self._root, "destroyPart", _DIRECT, Q_ARG("QVariant", part.model))
        part.model = None
        part.geometry = None

    def pose_part(self, handle: object, pose: EnginePose) -> None:
        model = self._model(handle)
        model.setProperty("position", QVector3D(*pose.position))
        model.setProperty("eulerRotation", QVector3D(0.0, pose.yaw_deg, 0.0))

    def tint_part(self, handle: object, tint_rgba: Rgba) -> None:
        model = self._model(handle)
        model.setProperty("tint", QColor(tint_rgba[0], tint_rgba[1], tint_rgba[2]))
        model.setProperty("tintLinear", QVector3D(*tint_uniform(tint_rgba)))

    def set_sun(self, travel: tuple[float, float, float] | None) -> None:
        if travel is None:
            self._root.setProperty("sunUp", False)
            return
        rotation = QQuaternion.rotationTo(QVector3D(*_LIGHT_FORWARD), QVector3D(*travel))
        self._root.setProperty("sunRotation", rotation)
        self._root.setProperty("sunUp", True)

    def set_ground(self, ground: GroundPlacement | None) -> None:
        if ground is None:
            self._root.setProperty("groundVisible", False)
            self._root.setProperty("groundCenter", QVector3D(0.0, 0.0, 0.0))
            return
        self._root.setProperty("groundCenter", QVector3D(*ground.center))
        self._root.setProperty("groundWidth", float(ground.width_cm))
        self._root.setProperty("groundDepth", float(ground.depth_cm))
        self._root.setProperty("groundVisible", True)

    def request_frame(self) -> None:
        if self._view.isVisible():
            self._view.update()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def container(self, parent: QWidget | None = None) -> QWidget:
        """The widget that embeds the view in a QWidget layout (created once).

        It wraps a NATIVE window: sibling widgets do not paint over it. After
        this call ``show()`` / ``hide()`` act on the container.
        """
        if self._container is None:
            from PyQt6.QtWidgets import QWidget

            self._container = QWidget.createWindowContainer(self._view, parent)
        return self._container

    def show(self) -> None:
        self._ever_shown = True
        if self._container is not None:
            self._container.show()
        else:
            self._view.show()

    def hide(self) -> None:
        if self._container is not None:
            self._container.hide()
        else:
            self._view.hide()

    def is_visible(self) -> bool:
        return bool(self._view.isVisible())

    @property
    def is_closed(self) -> bool:
        """True after ``close()`` — and when Qt destroyed the view itself, which happens
        when the widget that parents ``container()`` is destroyed. The host must not be
        used again."""
        return self._closed or sip.isdeleted(self._view)

    def wait_exposed(self, timeout_s: float = 10.0) -> bool:
        """Pump events until the shown window is exposed (the platform has mapped it).

        Returns False on timeout — a finding to log, never a hang. ``grab()`` does
        not need this on Windows, where it renders synchronously either way; a
        freshly shown X11 window is not ready for a grab before its expose.
        """
        started = time.perf_counter()
        deadline = started + timeout_s
        while not self._view.isExposed() and time.perf_counter() < deadline:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        exposed = bool(self._view.isExposed())
        self._log("exposed", ok=exposed, ms=round((time.perf_counter() - started) * 1000.0, 1))
        return exposed

    def close(self) -> None:
        """Destroy every Model, then the view (and its container). The host is dead after.

        Safe when Qt got there first (the container's parent widget was destroyed, and
        the window with it): then only the Python side is released.
        """
        if self._closed:
            return
        self._closed = True
        if sip.isdeleted(self._view):
            self._parts.clear()  # the Models died with the scene; let go of their geometries
            self._container = None
            return
        for part in list(self._parts):
            self.destroy_part(part)
        self._view.hide()
        if self._container is not None:
            self._container.deleteLater()  # owns the window
            self._container = None
        else:
            self._view.deleteLater()

    # ── frames ───────────────────────────────────────────────────────────────

    def grab(self, label: str = "") -> QImage:
        """Render the current state NOW and read it back (device pixels).

        Raises ``HostStateError`` where a grab would return something else than
        the current scene: a hidden host that was shown before, and the second
        grab of a host that was never shown (module docstring).
        """
        if not self._view.isVisible():
            if self._ever_shown:
                raise HostStateError("grab(): the host is hidden; show() it first")
            if self._hidden_grab_done:
                raise HostStateError(
                    "grab(): a host that was never shown renders ONE frame — the scene "
                    "graph is torn down after it. Build a new host, or show() this one")
            self._view.create()
            self._hidden_grab_done = True
        started = time.perf_counter()
        image = self._view.grabWindow()
        elapsed = (time.perf_counter() - started) * 1000.0
        self._log("grab", label=label, ms=round(elapsed, 1),
                  px=f"{image.width()}x{image.height()}", visible=self._view.isVisible())
        if image.isNull() or image.width() == 0 or image.height() == 0:
            raise EngineError(f"grab({label!r}) returned no image")
        self.grabs += 1
        return image

    def device_pixel_ratio(self) -> float:
        return float(self._view.effectiveDevicePixelRatio())

    def view_size(self) -> tuple[float, float]:
        """The view's actual LOGICAL size (a window larger than the screen is clamped)."""
        return float(self._root.width()), float(self._root.height())

    def graphics_api(self) -> str:
        """``Direct3D11Rhi``, ``OpenGLRhi``, … — or ``Unknown`` before a device exists."""
        interface = self._view.rendererInterface()
        if interface is None:
            return "Unknown"
        api = interface.graphicsApi()
        return str(getattr(api, "name", api))

    # ── camera and look ──────────────────────────────────────────────────────

    def set_shot(self, shot: Shot) -> None:
        if isinstance(shot, TopDownShot):
            self.set_top_down(shot.center, shot.px_per_cm)
        else:
            self.set_camera(shot.eye, shot.target, shot.fov_deg)

    def set_camera(self, eye_scene: tuple[float, float, float],
                   target_scene: tuple[float, float, float], fov_deg: float = 40.0) -> None:
        """Perspective: the eye and the point it looks at, scene frame (E, N, up)."""
        shot = PerspectiveShot(eye_scene, target_scene, fov_deg)  # validates
        self._root.setProperty("topDown", False)
        self._root.setProperty("camPos", _engine_vector(shot.eye))
        self._root.setProperty("camTarget", _engine_vector(shot.target))
        self._root.setProperty("camFov", float(shot.fov_deg))
        self._root.setProperty("camVersion", int(self._root.property("camVersion")) + 1)

    def set_top_down(self, center_scene: tuple[float, float], px_per_cm: float) -> None:
        """Orthographic, straight down, north up: the truth view (``shots.TopDownShot``)."""
        shot = TopDownShot(center_scene, px_per_cm)  # validates
        self._root.setProperty("camTarget", _engine_vector((*shot.center, 0.0)))
        self._root.setProperty("topDownScale", float(shot.px_per_cm))
        self._root.setProperty("topDown", True)

    def set_look(self, look: Look) -> None:
        """Apply a mood — plain data (``look.Look``); L1.4 passes its own."""
        self._root.setProperty("sunColor", QColor(look.sun_color))
        self._root.setProperty("sunBrightness", float(look.sun_brightness))
        self._root.setProperty("fillColor", QColor(look.fill_color))
        self._root.setProperty("exposure", float(look.exposure))
        self._root.setProperty("skyColor", QColor(look.sky_color))
        self._root.setProperty("meadowColor", QColor(look.meadow_albedo))
        self._root.setProperty("windStrength", float(look.wind_strength))

    # ── picking and readback ─────────────────────────────────────────────────

    def pick(self, x: float, y: float) -> PickHit | None:
        """The item under view position ``(x, y)`` (LOGICAL pixels), or None for
        empty space — for the frame rendered last.

        Raises ``HostStateError`` when there is no such frame (the host is hidden
        or nothing was rendered yet): "no hit" must never mean "could not look".
        """
        if not self._view.isVisible() or (self.grabs == 0 and self.frames_presented == 0):
            raise HostStateError("pick(): needs a shown host that has rendered a frame")
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
        by the ENGINE's projection of the frame rendered last."""
        engine = _engine_vector((east_cm, north_cm, up_cm))
        result = _js_object(QMetaObject.invokeMethod(
            self._root, "projectToView", _DIRECT, Q_RETURN_ARG("QVariant"),
            Q_ARG("QVariant", engine.x()), Q_ARG("QVariant", engine.y()),
            Q_ARG("QVariant", engine.z())))
        return float(result["x"]), float(result["y"])

    def engine_parts(self) -> list[EnginePart]:
        """Every live Model of the QML scene — what the engine holds, read from it."""
        rows = _js_object(QMetaObject.invokeMethod(
            self._root, "listParts", _DIRECT, Q_RETURN_ARG("QVariant"))) or []
        return [EnginePart(
            item_id=str(row["id"]), part_index=int(row["part"]), kind=str(row["kind"]),
            position=(float(row["x"]), float(row["y"]), float(row["z"])),
            yaw_deg=float(row["yaw"]),
            tint_rgb=(round(row["r"] * 255.0), round(row["g"] * 255.0), round(row["b"] * 255.0)),
            casts_shadow=bool(row["casts"]), has_geometry=bool(row["geometry"]),
        ) for row in rows]

    def scene_property(self, name: str) -> Any:
        """A root property of ``Garden.qml`` (tests and diagnostics)."""
        return self._root.property(name)

    # ── internals ────────────────────────────────────────────────────────────

    def _on_frame_swapped(self) -> None:
        self.frames_presented += 1

    def _part(self, handle: object) -> _Part:
        if not isinstance(handle, _Part) or handle.model is None:
            raise EngineError(f"not a live part of this host: {handle!r}")
        return handle

    def _model(self, handle: object) -> QObject:
        model = handle.model if isinstance(handle, _Part) else None
        if model is None:
            raise EngineError(f"not a live part of this host: {handle!r}")
        return model


def _engine_vector(point_scene: tuple[float, float, float]) -> QVector3D:
    engine = scene_to_engine_points(np.array(point_scene, dtype=np.float64))
    return QVector3D(float(engine[0]), float(engine[1]), float(engine[2]))
