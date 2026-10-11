"""``Quick3DHost`` without a GPU: the real QML scene, no pixels (Phase 17 L1.2, #385).

Under ``QT_QPA_PLATFORM=offscreen`` Qt Quick falls back to the software scene
graph, which draws no 3D — but ``Garden.qml`` still LOADS, Models are still
created, posed, tinted and destroyed, and their properties can be read back. So
this default-tier file checks everything about the engine boundary that is not a
pixel, on every machine and in the required CI job:

- the scene file and its part components load without a QML error;
- every sink call arrives as the right change of the right Model (read back from
  the QML scene, not from the sink's books);
- the sun, the ground, the camera and the look are mapped into the scene's
  properties in the engine frame;
- the host's state machine: which call is legal in which state, what a closed or
  destroyed host answers, the sink's teardown in every order;
- that a backend which cannot render 3D says so instead of handing out a frame.

What it cannot check — that any of it is DRAWN correctly, and that a wrong call
order no longer kills the process — is the render tier
(``tests/integration/test_quick3d_render.py``, ``test_quick3d_lifecycle.py``).

THE CANARY (review P1-2). A child process first creates a bare ``QQuickView``
with inline ``import QtQuick3D`` QML — ZERO product code. If that fails, this
platform cannot create Qt Quick 3D objects at all and the module skips (on CI it
FAILS: the runners are known to manage it, and a silent skip of this file is how
a broken engine package would ship green). Everything else — a QML error in
``Garden.qml``, a Python exception in the host — happens in the tests below and
FAILS them. The canary used to run the product: a broken ``Garden.qml`` gave 32
skips and 0 failures. ``test_a_broken_scene_file_fails_the_tier_instead_of_skipping_it``
is the positive control for that.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtGui import QColor, QQuaternion, QVector3D

from open_garden_planner.core.scene3d import (
    GroundSpec,
    Material,
    Mesh,
    MeshPart,
    Params,
    Record,
    RecordingSink,
    SceneSync,
    SunState,
    Transform,
    engine_buffers,
    prism_mesh,
)
from open_garden_planner.ui.view3d.quick3d.adapter import PartSpec, Quick3DSink
from open_garden_planner.ui.view3d.quick3d.look import NEUTRAL_DAY, Look
from open_garden_planner.ui.view3d.quick3d.shots import PerspectiveShot, TopDownShot
from tests.integration import quick3d_lifecycle_child as lifecycle

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
SQUARE = ((-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0))
RED = (230, 20, 20, 255)
BLUE = (20, 40, 230, 255)
#: Set by the positive control only: the QML directory the hosts of this file load.
QML_OVERRIDE = "OGP_TEST_QUICK3D_QML_DIR"
#: The three releases: legal on a closed host, where they do nothing.
RELEASES = ("destroy_part", "clear", "close")

# No product code in here — that is the point (module docstring). It does to bare Qt
# what the tests below do to the host: load a View3D scene from a file, create a Model
# with a geometry from Python, show and hide a window, embed one in a widget.
_CANARY = r'''
import sys, tempfile
from pathlib import Path
from PyQt6.QtCore import Q_ARG, Q_RETURN_ARG, QByteArray, QMetaObject, QObject, Qt, QUrl
from PyQt6.QtWidgets import QApplication, QWidget
app = QApplication(sys.argv[:1])
from PyQt6.QtQuick import QQuickView
from PyQt6.QtQuick3D import QQuick3DGeometry

QML = """
import QtQuick
import QtQuick3D
import QtQuick3D.Helpers
Item {
    width: 64; height: 48
    function make(geometry) { return part.createObject(parts, {"geometry": geometry}) }
    Component { id: part; Model { materials: PrincipledMaterial { } } }
    View3D {
        anchors.fill: parent
        environment: ExtendedSceneEnvironment { }
        PerspectiveCamera { z: 300 }
        DirectionalLight { }
        Node { id: parts }
    }
}
"""

class Geometry(QQuick3DGeometry):
    def __init__(self):
        super().__init__()
        self.setStride(12)
        self.setVertexData(QByteArray(bytes(36)))
        self.update()

def view_of(directory):
    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(str(Path(directory) / "Canary.qml")))
    assert view.rootObject() is not None, [error.toString() for error in view.errors()]
    return view

with tempfile.TemporaryDirectory() as directory:
    (Path(directory) / "Canary.qml").write_text(QML, encoding="utf-8")
    view = view_of(directory)
    geometry = Geometry()
    model = QMetaObject.invokeMethod(view.rootObject(), "make", Qt.ConnectionType.DirectConnection,
                                     Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", geometry))
    assert isinstance(model, QObject), model
    view.show(); app.processEvents(); view.hide(); app.processEvents()
    parent = QWidget()
    embedded = view_of(directory)
    container = QWidget.createWindowContainer(embedded, parent)
    parent.show(); app.processEvents(); parent.hide(); app.processEvents()
    parent.deleteLater(); view.deleteLater(); app.processEvents()
print("canary ok", flush=True)
'''


def _child_env() -> dict[str, str]:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC), str(REPO), env.get("PYTHONPATH")) if p)
    return env


@pytest.fixture(scope="module", autouse=True)
def quick3d_objects_can_be_created() -> None:
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own code
        [sys.executable, "-c", _CANARY], env=_child_env(), capture_output=True, text=True,
        timeout=170)
    if proc.returncode == 0 and "canary ok" in proc.stdout:
        return
    message = ("bare Qt cannot create Qt Quick 3D objects under the offscreen platform here "
               f"(no product code involved; exit {proc.returncode}): {proc.stderr[-800:]}")
    if os.environ.get("CI"):
        pytest.fail(message + "\nOn CI this is a failure, not a skip: the runners can, and a "
                    "skipped host tier would let a broken engine package through.")
    pytest.skip(message)


def test_the_canary_contains_no_product_code() -> None:
    """Review P1-2: the canary decides whether this file runs at all. If it imported the
    product, a bug in the product would turn every test here into a skip."""
    assert "open_garden_planner" not in _CANARY and "Garden.qml" not in _CANARY
    assert "import QtQuick3D" in _CANARY and "QQuickView()" in _CANARY


def _qml_dir() -> Path | None:
    override = os.environ.get(QML_OVERRIDE)
    return Path(override) if override else None


def _new_host(size: tuple[int, int] = (64, 48)) -> Any:
    from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

    return Quick3DHost(size, qml_dir=_qml_dir())


def _record(item_id: str, east: float = 0.0, north: float = 0.0, rotation: float = 0.0,
            colour: tuple[int, int, int, int] = RED, height: float | None = 100.0) -> Record:
    return Record(item_id=item_id, kind="SHED", shape="rectangle", footprints=(SQUARE,),
                  transform=Transform(east_cm=east, north_cm=north, rotation_deg=rotation),
                  material=Material("SHED", fill_rgba=colour), height_cm=height,
                  params=Params({"seed": item_id}))


@pytest.fixture()
def host(qtbot) -> Iterator[Any]:  # noqa: ARG001 - Qt must be initialised
    made = _new_host((320, 240))
    yield made
    made.close()


def _uploads() -> int:
    from open_garden_planner.ui.view3d.quick3d.geometry import BufferGeometry

    return int(BufferGeometry.uploads)


def _broken_copy(tmp_path: Path, old: str, new: str) -> Path:
    from open_garden_planner.ui.view3d.quick3d.host import QML_DIR

    broken = tmp_path / "qml"
    shutil.copytree(QML_DIR, broken)
    scene = broken / "Garden.qml"
    text = scene.read_text(encoding="utf-8")
    assert old in text, f"the control no longer finds {old!r} in Garden.qml"
    scene.write_text(text.replace(old, new), encoding="utf-8")
    return broken


class TestSceneFile:
    def test_garden_qml_loads_with_the_neutral_day_look(self, host) -> None:
        assert host.qml_load_ms > 0.0
        assert host.view_size() == (320.0, 240.0)
        assert host.scene_property("sunColor") == QColor(NEUTRAL_DAY.sun_color)
        assert host.scene_property("sunBrightness") == NEUTRAL_DAY.sun_brightness
        assert host.scene_property("fillColor") == QColor(NEUTRAL_DAY.fill_color)
        assert host.scene_property("exposure") == NEUTRAL_DAY.exposure
        assert host.scene_property("skyColor") == QColor(NEUTRAL_DAY.sky_color)
        assert host.scene_property("meadowColor") == QColor(NEUTRAL_DAY.meadow_albedo)
        assert host.scene_property("windTime") == 0.0, "the wind is at rest until L1.4 animates it"
        assert host.scene_property("sunUp") is False and host.scene_property("groundVisible") is False
        assert host.engine_parts() == []

    def test_a_look_is_applied_as_plain_data(self, host) -> None:
        golden = Look(sun_color="#ffb878", sun_brightness=1.7, fill_linear=(0.2, 0.1, 0.05),
                      exposure=1.15, sky_color="#f4d2a6", meadow_albedo="#335522",
                      wind_strength=0.0)
        host.set_look(golden)
        assert host.scene_property("sunColor") == QColor("#ffb878")
        assert host.scene_property("sunBrightness") == 1.7
        assert host.scene_property("fillColor") == QColor(golden.fill_color)
        assert host.scene_property("exposure") == 1.15
        assert host.scene_property("windStrength") == 0.0

    def test_a_scene_file_that_does_not_load_is_a_loud_error(self, qtbot, tmp_path: Path) -> None:  # noqa: ARG002
        """The Model/Light property-name trap as a load error, with file and line."""
        from open_garden_planner.ui.view3d.quick3d.host import EngineError, Quick3DHost

        broken = _broken_copy(tmp_path, "            castsShadow: true\n",
                              "            castsShadows: true\n")
        with pytest.raises(EngineError, match=r"Garden\.qml did not load.*castsShadows"):
            Quick3DHost((64, 48), qml_dir=broken)


class TestPropertiesAreChecked:
    """Review P2-2: every ``setProperty`` result used to be ignored. On a QML object a
    name the scene does not declare silently becomes a DYNAMIC property — the value
    goes nowhere, and nothing says so."""

    def test_a_renamed_scene_property_fails_the_host_instead_of_being_set_into_the_void(
            self, qtbot, tmp_path: Path) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import EngineError, Quick3DHost

        renamed = _broken_copy(tmp_path, "property real sunBrightness: 1.9",
                               "property real sunStrength: 1.9")
        with pytest.raises(EngineError, match="no property 'sunBrightness'"):
            Quick3DHost((64, 48), qml_dir=renamed)

    @pytest.mark.parametrize(("declaration", "name", "call"), [
        ("property bool sunUp: false", "sunUp", lambda h: h.set_sun((0.0, -1.0, 0.0))),
        ("property real groundWidth: 1000", "groundWidth",
         lambda h: SceneSync(Quick3DSink(h)).set_ground(GroundSpec(10.0, 10.0))),
        ("property bool topDown: false", "topDown",
         lambda h: h.set_shot(TopDownShot((0.0, 0.0), 0.5))),
        ("property real camFov: 40", "camFov",
         lambda h: h.set_camera((0.0, -5.0, 5.0), (0.0, 0.0, 0.0))),
    ], ids=["sun", "ground", "top-down", "camera"])
    def test_every_writer_names_the_property_it_could_not_set(
            self, qtbot, tmp_path: Path, declaration: str, name: str, call: Any) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import EngineError, Quick3DHost

        renamed = _broken_copy(tmp_path, declaration, declaration.replace(name, name + "Renamed"))
        made = Quick3DHost((64, 48), qml_dir=renamed)
        try:
            with pytest.raises(EngineError, match=f"no property '{name}'"):
                call(made)
        finally:
            made.close()

    def test_a_model_without_a_tint_property_is_an_error_and_leaves_no_orphan(
            self, qtbot, tmp_path: Path) -> None:  # noqa: ARG002
        from open_garden_planner.core.scene3d import engine_pose
        from open_garden_planner.ui.view3d.quick3d.host import EngineError, Quick3DHost

        renamed = _broken_copy(tmp_path, "property vector3d tintLinear: Qt.vector3d(1, 1, 1)",
                               "property vector3d tintLinearRenamed: Qt.vector3d(1, 1, 1)")
        made = Quick3DHost((64, 48), qml_dir=renamed)
        try:
            buffers = engine_buffers(prism_mesh([SQUARE], 10.0))
            with pytest.raises(EngineError, match="no property 'tintLinear'"):
                made.create_part(PartSpec("a", 0, "vc", buffers, True), engine_pose(Transform()), RED)
            assert made.engine_parts() == [], "the half-made Model must not stay in the scene"
        finally:
            made.close()

    def test_a_value_the_property_cannot_take_is_refused(self, host) -> None:
        from open_garden_planner.ui.view3d.quick3d import host as host_module

        with pytest.raises(host_module.EngineError, match="refused .* for its property 'groundWidth'"):
            host_module._set(host._root, "groundWidth", QColor("red"))  # noqa: SLF001
        with pytest.raises(host_module.EngineError, match="no property 'noSuchProperty'"):
            host.scene_property("noSuchProperty")


class TestSinkCallsReachTheQmlScene:
    def test_add_creates_one_model_per_part_with_pose_tint_and_geometry(self, host) -> None:
        sync = SceneSync(Quick3DSink(host), validate=True)
        before = _uploads()
        sync.apply({"a": _record("a", 300.0, 120.0, 17.0), "flat": _record("flat", height=None)})
        (part,) = host.engine_parts()
        assert (part.item_id, part.part_index, part.kind) == ("a", 0, "vc")
        assert part.position == pytest.approx((300.0, 0.0, -120.0))   # (E, base, −N)
        assert part.yaw_deg == pytest.approx(17.0)                    # eulerRotation.y = +rotation
        assert part.tint_rgb == RED[:3] and part.casts_shadow and part.has_geometry
        assert part.visible is True
        assert _uploads() - before == 1

    def test_every_material_kind_gets_a_model(self, host) -> None:
        sink = Quick3DSink(host)
        mesh = prism_mesh([SQUARE], 20.0)
        kinds = ("vc", "roof", "foliage", "grass", "glass", "water")
        sink.begin()
        sink.add("all", tuple(MeshPart(mesh, kind, casts_shadow=kind != "glass", tinted=True)
                              for kind in kinds), Transform(), Material("SHED", fill_rgba=BLUE))
        sink.commit()
        parts = sorted(host.engine_parts(), key=lambda p: p.part_index)
        assert tuple(p.kind for p in parts) == kinds
        assert all(p.tint_rgb == BLUE[:3] for p in parts)
        assert [p.casts_shadow for p in parts] == [k != "glass" for k in kinds]

    def test_move_recolour_replace_remove_and_clear(self, host) -> None:
        sink = Quick3DSink(host)
        recording = RecordingSink()
        syncs = (SceneSync(sink), SceneSync(recording))

        def apply(records: dict[str, Record]) -> int:
            before = _uploads()
            for sync in syncs:
                sync.apply(records)
            assert sink.state() == recording.state()
            return _uploads() - before

        assert apply({"a": _record("a"), "b": _record("b", 400.0, colour=BLUE)}) == 2
        assert apply({"a": _record("a", -250.0, -150.0, 30.0), "b": _record("b", 400.0, colour=BLUE)}) == 0
        moved = next(p for p in host.engine_parts() if p.item_id == "a")
        assert moved.position == pytest.approx((-250.0, 0.0, 150.0)) and moved.yaw_deg == pytest.approx(30.0)
        assert apply({"a": _record("a", -250.0, -150.0, 30.0, colour=(250, 220, 10, 255)),
                      "b": _record("b", 400.0, colour=BLUE)}) == 0   # a recolour uploads nothing
        assert next(p for p in host.engine_parts() if p.item_id == "a").tint_rgb == (250, 220, 10)
        assert apply({"a": _record("a", -250.0, -150.0, 30.0, colour=(250, 220, 10, 255), height=300.0),
                      "b": _record("b", 400.0, colour=BLUE)}) == 1   # new geometry: one fresh upload
        assert len(host.engine_parts()) == 2
        assert apply({"b": _record("b", 400.0, colour=BLUE)}) == 0
        assert [p.item_id for p in host.engine_parts()] == ["b"]
        syncs[0].set_sun(SunState(40.0, 180.0))
        syncs[0].set_ground(GroundSpec(100.0, 100.0))
        sink.clear()
        assert host.engine_parts() == []
        assert host.scene_property("sunUp") is False and host.scene_property("groundVisible") is False

    def test_a_dead_handle_and_an_unknown_kind_are_refused(self, host) -> None:
        from open_garden_planner.core.scene3d import engine_pose
        from open_garden_planner.ui.view3d.quick3d.host import EngineError

        buffers = engine_buffers(prism_mesh([SQUARE], 10.0))
        pose = engine_pose(Transform())
        handle = host.create_part(PartSpec("a", 0, "vc", buffers, True), pose, RED)
        host.destroy_part(handle)
        for call in (lambda: host.destroy_part(handle), lambda: host.pose_part(handle, pose),
                     lambda: host.tint_part(handle, RED), lambda: host.pose_part(object(), pose)):
            with pytest.raises(EngineError, match="not a live part"):
                call()
        with pytest.raises(EngineError, match="no part component for material kind 'lava'"):
            host.create_part(PartSpec("a", 0, "lava", buffers, True), pose, RED)
        assert host.engine_parts() == []

    def test_a_hidden_model_is_still_listed_and_a_destroyed_one_is_not(self, host) -> None:
        """Review P2-10: ``listParts()`` treated "invisible" as "destroyed". A Model a later
        story hides on purpose is still the engine's — listed, ``visible=False``; a
        destroyed one is gone from the list at once, before its deferred delete."""
        from open_garden_planner.core.scene3d import engine_pose

        buffers = engine_buffers(prism_mesh([SQUARE], 10.0))
        kept = host.create_part(PartSpec("kept", 0, "vc", buffers, True), engine_pose(Transform()), RED)
        gone = host.create_part(PartSpec("gone", 0, "vc", buffers, True), engine_pose(Transform()), RED)
        assert kept.model.setProperty("visible", False)
        host.destroy_part(gone)
        (part,) = host.engine_parts()  # no event loop ran: the destroyed Model still exists in Qt
        assert (part.item_id, part.visible, part.has_geometry) == ("kept", False, True)

    def test_the_host_owns_every_model_and_clear_destroys_them_all(self, host) -> None:
        """One owner of Model lifetime (review P1-3): also parts no sink knows about."""
        from open_garden_planner.core.scene3d import engine_pose

        buffers = engine_buffers(prism_mesh([SQUARE], 10.0))
        handles = [host.create_part(PartSpec(str(k), 0, "vc", buffers, True),
                                    engine_pose(Transform()), RED) for k in range(3)]
        host.set_sun((0.0, -1.0, 0.0))
        assert len(host.engine_parts()) == 3 and host.scene_property("sunUp") is True
        host.clear()
        assert host.engine_parts() == [] and host.scene_property("sunUp") is False
        assert all(handle.model is None and handle.geometry is None for handle in handles)
        host.clear()  # idempotent


class TestSunGroundCamera:
    @pytest.mark.parametrize(("elevation", "azimuth"), [(35.0, 225.0), (60.0, 180.0), (12.0, 95.0),
                                                        (90.0, 0.0)])
    def test_the_light_shines_along_the_suns_travel(self, host, elevation: float,
                                                    azimuth: float) -> None:
        """A DirectionalLight shines down its local −Z; the rotation the host hands the
        scene must turn −Z onto the light's travel — the negated sun vector, in the
        engine frame."""
        sun = SunState(elevation, azimuth)
        sync = SceneSync(Quick3DSink(host))
        sync.set_sun(sun)
        assert host.scene_property("sunUp") is True
        rotation: QQuaternion = host.scene_property("sunRotation")
        shines = rotation.rotatedVector(QVector3D(0.0, 0.0, -1.0))
        east, north, up = sun.light_travel_scene
        assert (shines.x(), shines.y(), shines.z()) == pytest.approx((east, up, -north), abs=1e-5)
        assert shines.y() < 0.0  # downwards

    def test_a_sun_that_is_down_switches_the_light_off(self, host) -> None:
        sync = SceneSync(Quick3DSink(host))
        sync.set_sun(SunState(40.0, 180.0))
        sync.set_sun(SunState(-6.0, 300.0))
        assert host.scene_property("sunUp") is False

    def test_the_ground_is_the_plan_rectangle(self, host) -> None:
        sync = SceneSync(Quick3DSink(host))
        sync.set_ground(GroundSpec(2400.0, 1600.0))
        assert host.scene_property("groundVisible") is True
        center: QVector3D = host.scene_property("groundCenter")
        assert (center.x(), center.y(), center.z()) == (1200.0, 0.0, -800.0)
        assert host.scene_property("groundWidth") == 2400.0
        assert host.scene_property("groundDepth") == 1600.0

    def test_cameras_are_placed_in_the_engine_frame(self, host) -> None:
        host.set_shot(PerspectiveShot(eye=(100.0, -700.0, 420.0), target=(10.0, 20.0, 30.0), fov_deg=50.0))
        eye, target = host.scene_property("camPos"), host.scene_property("camTarget")
        assert (eye.x(), eye.y(), eye.z()) == (100.0, 420.0, 700.0)        # (E, up, −N)
        assert (target.x(), target.y(), target.z()) == (10.0, 30.0, -20.0)
        assert host.scene_property("camFov") == 50.0 and host.scene_property("topDown") is False
        version = host.scene_property("camVersion")
        host.set_shot(TopDownShot(center=(1200.0, 800.0), px_per_cm=0.25))
        target = host.scene_property("camTarget")
        assert (target.x(), target.z()) == (1200.0, -800.0)
        assert host.scene_property("topDown") is True and host.scene_property("topDownScale") == 0.25
        host.set_camera((0.0, -500.0, 300.0), (0.0, 0.0, 0.0))
        assert host.scene_property("camVersion") == version + 1 and host.scene_property("camFov") == 40.0

    def test_a_shot_that_cannot_be_rendered_is_refused(self, host) -> None:
        with pytest.raises(ValueError, match="same point"):
            host.set_camera((1.0, 2.0, 3.0), (1.0, 2.0, 3.0))
        with pytest.raises(ValueError, match="px_per_cm"):
            host.set_top_down((0.0, 0.0), 0.0)


def _force(host: Any, *, scene_graph: bool, visible: bool, exposed: bool, spent: bool = False,
           renders_3d: bool = True) -> None:
    """Put Qt's predicates where a real window system would (the offscreen platform
    cannot: its scene graph exists from birth and it renders no 3D). The state machine
    is a pure function of them — which is what makes it testable here; that the
    predicates DO take these values, and what happens then, is the render tier's."""
    view = host._view  # noqa: SLF001
    view.isSceneGraphInitialized = lambda: scene_graph
    view.isVisible = lambda: visible
    view.isExposed = lambda: exposed
    host._spent = spent  # noqa: SLF001
    if renders_3d:
        host.graphics_api = lambda: "Direct3D11"


class TestStateMachine:
    """Review P1-1. The states are derived from Qt on every call; no Python flag says
    "shown" (the parent of an embedded container shows and hides it without asking)."""

    @pytest.mark.parametrize(("scene_graph", "visible", "exposed", "spent", "expected"), [
        (False, False, False, False, "new"),
        (False, True, False, False, "starting"),    # shown, first expose pending
        (False, True, True, False, "starting"),     # no scene graph: never a frame
        (True, True, True, False, "live"),
        (True, False, True, False, "hidden"),       # right after hide(): Qt still says exposed
        (True, True, False, False, "hidden"),       # minimised, or shown again a moment ago
        (True, False, False, False, "hidden"),
        (False, False, False, True, "spent"),
        (False, True, False, True, "spent"),
        (True, True, True, True, "spent"),
    ])
    def test_the_state_is_a_function_of_qts_predicates(
            self, host, scene_graph: bool, visible: bool, exposed: bool, spent: bool,
            expected: str) -> None:
        _force(host, scene_graph=scene_graph, visible=visible, exposed=exposed, spent=spent)
        assert host.state.value == expected
        assert host.is_closed is False and host.is_visible() is visible

    def test_a_shown_window_without_a_scene_graph_refuses_a_frame(self, host) -> None:
        """THE guard. ``show()`` then ``grab()`` before the first expose returned a good
        image and killed the process (5 of 5 on a GPU, 3 of 3 on WARP, 4 of 4 embedded).
        The old guard asked ``isVisible()`` — which is True there."""
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        _force(host, scene_graph=False, visible=True, exposed=False)
        for call in (lambda: host.grab("starting"), lambda: host.pick(1.0, 1.0),
                     lambda: host.project(0.0, 0.0, 0.0)):
            with pytest.raises(HostStateError, match=r"would kill the process.*wait_ready\(\)"):
                call()
        assert host.grabs == 0 and host.state.value == "starting"  # and it did not become spent

    def test_a_hidden_host_refuses_a_frame(self, host) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        _force(host, scene_graph=True, visible=False, exposed=False)
        for call in (lambda: host.grab("hidden"), lambda: host.pick(1.0, 1.0),
                     lambda: host.project(0.0, 0.0, 0.0)):
            with pytest.raises(HostStateError, match="not exposed"):
                call()

    def test_a_spent_host_refuses_everything_that_would_kill_it(self, host) -> None:
        """One hidden grab, then ``show()`` — what the old message recommended — killed
        the process. A spent host says: build a new one."""
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        _force(host, scene_graph=False, visible=False, exposed=False, spent=True)
        for call in (host.show, host.container, lambda: host.grab("again"),
                     lambda: host.pick(1.0, 1.0)):
            with pytest.raises(HostStateError, match="Build a new host") as caught:
                call()
            assert "show() it" not in str(caught.value)
        assert host.wait_ready(5.0) is False  # at once: nothing is on its way
        host.hide()                           # harmless

    def test_an_embedded_host_has_no_offscreen_frame(self, host, qtbot) -> None:
        """Its parent may show it at any moment — and then the process would die."""
        from PyQt6.QtWidgets import QWidget

        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        parent = QWidget()
        qtbot.addWidget(parent)
        host.container(parent)
        _force(host, scene_graph=False, visible=False, exposed=False)
        with pytest.raises(HostStateError, match="embedded host has no offscreen frame"):
            host.grab("embedded")
        assert host.state.value == "new", "the refusal must not spend the host"

    def test_wait_ready_reports_instead_of_hanging(self, host) -> None:
        assert host.wait_ready(5.0) is False, "a host nobody showed is not on its way"
        host.show()
        assert host.wait_ready(5.0) is True and host.state.value == "live"
        host.hide()
        assert host.state.value == "hidden", "hidden at once, whatever Qt still calls exposed"
        assert host.wait_ready(5.0) is False

    def test_wait_ready_times_out_on_a_window_that_never_starts(self, host) -> None:
        _force(host, scene_graph=False, visible=True, exposed=False)
        assert host.wait_ready(0.15) is False and host.state.value == "starting"

    def test_request_frame_is_harmless_in_every_open_state(self, host) -> None:
        host.request_frame()
        host.show()
        assert host.wait_ready(5.0)
        host.request_frame()
        host.hide()
        host.request_frame()

    def test_the_container_embeds_the_view_and_takes_over_show_and_hide(self, host, qtbot) -> None:
        from PyQt6.QtWidgets import QVBoxLayout, QWidget

        parent = QWidget()
        qtbot.addWidget(parent)
        layout = QVBoxLayout(parent)
        container = host.container(parent)
        assert host.container() is container, "the container is created once"
        layout.addWidget(container)
        parent.resize(400, 300)
        parent.show()
        host.show()
        qtbot.waitUntil(container.isVisible, timeout=5000)
        assert host.wait_ready(5.0) and host.state.value == "live"
        container.hide()  # the PARENT's side hides it: host.hide() is never called
        assert host.state.value == "hidden" and not host.is_visible()
        container.show()
        assert host.wait_ready(5.0)
        host.hide()
        assert not container.isVisible()


class TestABackendWithout3d:
    """Under the software scene graph Qt Quick 3D draws nothing. A frame without the
    garden must not pass for one — and here that backend is the one we run on."""

    def test_the_backend_is_named_from_qts_enum(self, host) -> None:
        from PyQt6.QtQuick import QSGRendererInterface

        from open_garden_planner.ui.view3d.quick3d.host import GRAPHICS_API_NAMES, RENDERING_APIS

        assert host.graphics_api() == "Software" and host.renders_3d() is False
        # the CI finding: a list of guessed strings said "OpenGLRhi"; PyQt says "OpenGL"
        members = list(QSGRendererInterface.GraphicsApi)
        assert set(GRAPHICS_API_NAMES) == set(members), "every API Qt knows has a name"
        assert len(set(GRAPHICS_API_NAMES.values())) == len(GRAPHICS_API_NAMES)
        for member, name in GRAPHICS_API_NAMES.items():
            assert member.name in (name, name + "Rhi"), (member, name)
        rhi = {name for member, name in GRAPHICS_API_NAMES.items()
               if QSGRendererInterface.isApiRhiBased(member)}
        assert rhi - {"Null"} == RENDERING_APIS

    def test_no_frame_no_pick_no_projection(self, host) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import EngineError

        SceneSync(Quick3DSink(host)).apply({"a": _record("a")})
        host.show()
        assert host.wait_ready(5.0)
        for call in (lambda: host.grab("software"), lambda: host.pick(5.0, 5.0),
                     lambda: host.project(0.0, 0.0, 0.0)):
            with pytest.raises(EngineError, match="Software backend.*draws nothing"):
                call()
        assert host.grabs == 0

    def test_render_records_refuses_and_leaves_the_process_as_it_found_it(
            self, qtbot, capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ARG002
        """Review P2-4: no output unless a log is passed; the message handler and Qt's
        logging rules are put back — also when the render fails, as it must here."""
        from PyQt6.QtCore import QLoggingCategory, qInstallMessageHandler

        from open_garden_planner.ui.view3d.quick3d.offscreen import RenderError, render_records

        def mine(*_args: object) -> None:
            return None

        before = qInstallMessageHandler(mine)
        try:
            with pytest.raises(RenderError, match="Software backend.*no 3D frame"):
                render_records({"a": _record("a")}, shot=TopDownShot((0.0, 0.0), 0.5),
                               sun=SunState(45.0, 225.0), size=(160, 120), graphics_info=True,
                               qml_dir=_qml_dir())
            after = qInstallMessageHandler(before)
        except BaseException:
            qInstallMessageHandler(before)
            raise
        assert after is mine, "the caller's message handler was not put back"
        assert not QLoggingCategory("qt.rhi.general").isDebugEnabled(), "the debug rule stayed on"
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""

    def test_render_records_reports_to_a_log_that_is_passed(self, qtbot) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.offscreen import RenderError, render_records

        phases: list[str] = []
        with pytest.raises(RenderError):
            render_records({}, shot=TopDownShot((0.0, 0.0), 0.5), qml_dir=_qml_dir(),
                           log=lambda phase, **_fields: phases.append(phase))
        assert phases[0] == "qml_loaded" and phases[-1] == "render_failed"

    def test_a_broken_scene_file_fails_the_render(self, qtbot, tmp_path: Path) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import EngineError
        from open_garden_planner.ui.view3d.quick3d.offscreen import render_records

        broken = _broken_copy(tmp_path, "import QtQuick3D.Helpers\n",
                              "import QtQuick3D.Helpers\nimport NoSuchModule\n")
        with pytest.raises(EngineError, match=r"Garden\.qml did not load"):
            render_records({}, shot=TopDownShot((0.0, 0.0), 0.5), qml_dir=broken)

    def test_an_empty_mesh_part_has_no_model(self, host) -> None:
        sink = Quick3DSink(host)
        sink.begin()
        sink.add("a", (MeshPart(Mesh.empty(), "vc"), MeshPart(prism_mesh([SQUARE], 5.0), "roof")),
                 Transform(), Material("SHED"))
        sink.commit()
        (part,) = host.engine_parts()
        assert (part.part_index, part.kind) == (1, "roof")


def _closed_host_answers(host: Any, part: Any) -> dict[str, Any]:
    lifecycle._register_calls()  # noqa: SLF001 - the lifecycle matrix's own table
    return {name: lifecycle._attempt(lambda call=call: call(host, part))  # noqa: SLF001
            for name, call in lifecycle.HOST_CALLS.items()}


def _assert_closed_contract(host: Any, part: Any) -> None:
    assert host.state.value == "closed" and host.is_closed and not host.is_visible()
    for name, answer in _closed_host_answers(host, part).items():
        if name in RELEASES:
            assert answer is None, f"{name}() on a closed host must be a silent no-op: {answer!r}"
        else:
            assert isinstance(answer, str) and answer.startswith("HostStateError: "), (name, answer)
            assert "closed" in answer, (name, answer)


class TestClosedAndDestroyedHosts:
    """Review P1-3: ONE owner of Model lifetime. ``destroy_part`` / ``clear`` / ``close``
    on a closed or destroyed host do nothing; every other call raises ``HostStateError``
    — never sip's ``RuntimeError`` or a ``TypeError`` from a dead C++ object."""

    def _embedded(self, qtbot) -> tuple[Any, Quick3DSink, SceneSync, Any, Any]:  # noqa: ARG002
        from PyQt6.QtWidgets import QWidget

        from open_garden_planner.core.scene3d import engine_pose

        made = _new_host()
        sink = Quick3DSink(made)
        sync = SceneSync(sink)
        sync.apply({"a": _record("a"), "b": _record("b", 200.0)})
        sync.set_sun(SunState(40.0, 180.0))
        sync.set_ground(GroundSpec(500.0, 500.0))
        part = made.create_part(PartSpec("p", 0, "vc", engine_buffers(prism_mesh([SQUARE], 10.0)),
                                         True), engine_pose(Transform()), RED)
        parent = QWidget()
        made.container(parent)
        return made, sink, sync, parent, part

    def test_the_call_table_covers_every_public_call(self) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

        lifecycle._register_calls()  # noqa: SLF001
        public = {name for name in dir(Quick3DHost)
                  if not name.startswith("_") and callable(getattr(Quick3DHost, name))}
        assert set(lifecycle.HOST_CALLS) == public - {"is_visible", "engine_errors"}

    def test_close_then_anything(self, qtbot) -> None:
        made, _sink, _sync, parent, part = self._embedded(qtbot)
        assert len(made.engine_parts()) == 3 and not made.is_closed
        made.close()
        _assert_closed_contract(made, part)
        assert part.model is None and part.geometry is None, "close() released every part"
        parent.deleteLater()

    def test_parent_destroyed_then_anything(self, qtbot) -> None:
        """A window container belongs to its parent widget: when that widget is destroyed
        (closing the main window) Qt destroys the view with it. The host learns it by
        asking Qt — nobody told it."""
        from PyQt6 import sip

        made, _sink, _sync, parent, part = self._embedded(qtbot)
        sip.delete(parent)
        _assert_closed_contract(made, part)
        made.close()
        _assert_closed_contract(made, part)

    @pytest.mark.parametrize("order", ["parent destroyed, clear", "parent destroyed, close, clear",
                                       "close, clear"])
    def test_the_sink_clears_cleanly_in_every_order(self, qtbot, order: str) -> None:
        """The three orders of the review, each of which raised: ``TypeError``,
        ``ValueError``, and ``EngineError`` with the sink's books left dirty."""
        from PyQt6 import sip

        made, sink, sync, parent, _part = self._embedded(qtbot)
        if order.startswith("parent destroyed"):
            sip.delete(parent)
        if "close" in order:
            made.close()
        sink.clear()
        assert sink.item_ids() == () and sink.sun is None and sink.ground is None
        assert not sink.in_transaction
        sink.clear()   # idempotent
        made.close()   # and close() after the teardown is quiet, in every order
        sink.clear()
        sync.reset()
        assert made.is_closed

    def test_a_fresh_host_takes_over_after_the_old_one_died(self, qtbot) -> None:
        """The recovery path: the SAME sink and sync, cleared and reset on a dead host,
        attached to a fresh one and applied again."""
        from PyQt6 import sip

        made, sink, sync, parent, _part = self._embedded(qtbot)
        records = {"a": _record("a"), "b": _record("b", 200.0)}
        sip.delete(parent)
        sink.clear()
        sync.reset()
        fresh = _new_host()
        try:
            sink.attach(fresh)
            sync.apply(records)
            sync.set_sun(SunState(40.0, 180.0))
            assert sorted(p.item_id for p in fresh.engine_parts()) == ["a", "b"]
            assert fresh.scene_property("sunUp") is True
        finally:
            fresh.close()

    def test_close_destroys_every_model_and_is_idempotent(self, qtbot) -> None:  # noqa: ARG002
        made = _new_host()
        sink = Quick3DSink(made)
        SceneSync(sink).apply({"a": _record("a"), "b": _record("b", 200.0)})
        assert len(made.engine_parts()) == 2 and not made.is_closed
        made.close()
        assert made.is_closed
        made.close()  # a second close is a no-op, not an error on a deleted view


class TestLiveErrors:
    """Review P2-13: a seam for errors in a view that is SHOWN (nobody to raise to)."""

    def test_without_a_watch_there_is_no_answer(self, host) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        with pytest.raises(HostStateError, match=r"watch_messages\(\) first"):
            host.engine_errors()

    def test_a_shader_error_reaches_the_log_and_the_owner(
            self, host, caplog: pytest.LogCaptureFixture) -> None:
        from PyQt6.QtCore import qInstallMessageHandler, qWarning

        logger = logging.getLogger("ogp.tests.quick3d.host.watch")

        def silencer(*_args: object) -> None:  # keeps the test's stderr clean
            return None

        quiet = qInstallMessageHandler(silencer)
        try:
            watch = host.watch_messages(logger)
            assert host.watch_messages(logger) is watch, "one watch per host"
            assert host.engine_errors() == []
            with caplog.at_level(logging.DEBUG, logger=logger.name):
                qWarning("QSpirvCompiler: Failed to parse shader")
                qWarning("an ordinary Qt warning")
            assert host.engine_errors() == ["warning: QSpirvCompiler: Failed to parse shader"]
            assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
                (logging.ERROR, "Qt: warning: QSpirvCompiler: Failed to parse shader"),
                (logging.WARNING, "Qt: warning: an ordinary Qt warning")]
            host.close()
            qWarning("QSpirvCompiler: after close")   # nobody listens any more
            assert len(host.engine_errors()) == 1, "readable after close(), and no longer recording"
        finally:
            restored = qInstallMessageHandler(quiet)
        assert restored is silencer, "close() must put the handler back that was there before"

    def test_the_default_logger_is_the_apps(self, host, caplog: pytest.LogCaptureFixture) -> None:
        """No logger passed: the host's module logger — a child of the root logger that
        ``core.logging_config`` gives the rotating ``app.log`` handler."""
        from PyQt6.QtCore import qInstallMessageHandler, qWarning

        from open_garden_planner.ui.view3d.quick3d import host as host_module

        quiet = qInstallMessageHandler(lambda *_args: None)
        try:
            host.watch_messages()
            with caplog.at_level(logging.WARNING, logger=host_module.__name__):
                qWarning("Failed to compile fragment shader")
            host.close()
        finally:
            qInstallMessageHandler(quiet)
        assert [r.name for r in caplog.records] == [host_module.__name__]
        assert host_module.__name__.startswith("open_garden_planner.")


class TestGeometry:
    def test_buffers_are_handed_to_qt_unchanged(self, qtbot) -> None:  # noqa: ARG002
        from PyQt6.QtQuick3D import QQuick3DGeometry

        from open_garden_planner.ui.view3d.quick3d.geometry import BufferGeometry

        buffers = engine_buffers(prism_mesh([SQUARE], 100.0))
        before = BufferGeometry.uploads
        geometry = BufferGeometry(buffers)
        assert BufferGeometry.uploads == before + 1 and geometry.vertex_count == buffers.vertex_count
        assert geometry.stride() == 48
        assert bytes(geometry.vertexData()) == buffers.vertex_data
        assert bytes(geometry.indexData()) == buffers.index_data
        assert geometry.primitiveType() == QQuick3DGeometry.PrimitiveType.Triangles
        assert geometry.attributeCount() == 5
        low, high = geometry.boundsMin(), geometry.boundsMax()
        assert (low.x(), low.y(), low.z()) == buffers.bounds_min
        assert (high.x(), high.y(), high.z()) == buffers.bounds_max
        semantic = QQuick3DGeometry.Attribute.Semantic
        layout = {geometry.attribute(i).semantic: geometry.attribute(i).offset for i in range(5)}
        assert layout == {semantic.PositionSemantic: 0, semantic.NormalSemantic: 12,
                          semantic.ColorSemantic: 24, semantic.TexCoordSemantic: 40,
                          semantic.IndexSemantic: 0}

    def test_there_is_no_way_to_refill_a_geometry(self) -> None:
        """One geometry per Model lifetime, structurally: the class offers no method that
        uploads again (``ogp-3d-renderer`` §2 — a reused geometry rendered nothing)."""
        from open_garden_planner.ui.view3d.quick3d.geometry import BufferGeometry

        own = {name for name, value in vars(BufferGeometry).items() if callable(value)}
        assert own == {"__init__"}


# ── the positive control for the canary (review P1-2) ────────────────────────

_CONTROL = "test_a_broken_scene_file_fails_the_tier_instead_of_skipping_it"


def test_a_broken_scene_file_fails_the_tier_instead_of_skipping_it(tmp_path: Path) -> None:
    """THIS file, run again in a child pytest with every host pointed at a ``Garden.qml``
    that does not load. With the old canary — which loaded the product's scene itself —
    that run reported ``32 skipped`` and exit code 0: a broken engine package, green.
    Now it must FAIL, and skip nothing."""
    broken = _broken_copy(tmp_path, "import QtQuick3D.Helpers\n",
                          "import QtQuick3D.Helpers\nimport NoSuchModule\n")
    env = _child_env()
    env[QML_OVERRIDE] = str(broken)
    env.pop("CI", None)  # the canary's CI rule is not what is under test here
    this = Path(__file__).resolve()
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own code
        [sys.executable, "-m", "pytest", str(this), "-q", "-p", "no:cacheprovider",
         "--deselect", f"{this.relative_to(REPO).as_posix()}::{_CONTROL}"],
        env=env, capture_output=True, text=True, timeout=170, cwd=REPO)
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    assert proc.returncode == 1, f"exit {proc.returncode}\n{proc.stdout[-3000:]}\n{proc.stderr[-1500:]}"
    broken_tests = sum(int(n) for n, _ in re.findall(r"(\d+) (failed|errors?)\b", summary))
    assert broken_tests >= 30, summary
    assert "skipped" not in summary, f"a broken scene file must not be skipped away: {summary}"
    assert "did not load" in proc.stdout, "and the failure names the cause"
