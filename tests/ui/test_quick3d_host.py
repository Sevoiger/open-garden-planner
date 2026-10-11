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
  destroyed host answers, the sink's teardown in every order — also when the engine
  REFUSES to destroy a Model;
- ``health()``: what Qt reports about a host on the GUI thread;
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
import time
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
from tests.integration.quick3d_render_support import isolated_env

# Qt's QML engine writes a compiled copy of every scene file it loads into the per-user
# cache (<cache>/qmlcache/*.qmlc) - from C++, so nothing in Python redirects it - and this
# file loads a fresh copy of Garden.qml for every control: hundreds of files in the
# developer's profile after a few runs. No QML disk cache for this test process. Set at
# import, during collection, before any QML engine exists (Qt reads the variable once).
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

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


def _child_env(scratch: Path) -> dict[str, str]:
    """A child that cannot reach the developer's data (``isolated_env``), offscreen."""
    return isolated_env(scratch, QT_QPA_PLATFORM="offscreen")


@pytest.fixture(scope="module", autouse=True)
def quick3d_objects_can_be_created(tmp_path_factory: pytest.TempPathFactory) -> None:
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own code
        [sys.executable, "-c", _CANARY], env=_child_env(tmp_path_factory.mktemp("canary")),
        capture_output=True, text=True, timeout=170)
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

    def test_a_destroyed_model_is_deleted_not_only_retired(self, host) -> None:
        """Destruction, counted in the QML scene itself. A Model that was only RETIRED —
        flagged, unpickable, never deleted — is invisible to ``engine_parts()`` (which
        skips retired Models by design) and to every pixel test (its geometry dies with
        the Python handle, so it draws nothing): a leak nothing else would see. Found by
        a mutant that survived the render tier."""
        from PyQt6.QtCore import QCoreApplication, QEvent, QObject

        from open_garden_planner.core.scene3d import engine_pose

        def models_alive() -> int:
            """Every part Model object in the scene, retired ones included."""
            return sum(1 for child in host._root.findChildren(QObject)  # noqa: SLF001
                       if child.metaObject().indexOfProperty("retired") >= 0)

        def deliver_deferred_deletes() -> None:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)

        buffers = engine_buffers(prism_mesh([SQUARE], 10.0))
        handles = [host.create_part(PartSpec(str(k), 0, "vc", buffers, True),
                                    engine_pose(Transform()), RED) for k in range(3)]
        assert models_alive() == 3
        host.destroy_part(handles[0])
        host.destroy_part(handles[1])
        assert len(host.engine_parts()) == 1, "gone from the listing at once"
        deliver_deferred_deletes()
        assert models_alive() == 1, "and gone from the scene once Qt delivered the delete"
        host.clear()
        deliver_deferred_deletes()
        assert models_alive() == 0

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


def _facts(**changes: Any) -> Any:
    """Qt's facts as a live, answerable 320 x 240 window reports them — with ``changes``
    (``size=(w, h)`` sets the WINDOW's size and leaves the scene's at 320 x 240)."""
    from open_garden_planner.ui.view3d.quick3d.lifecycle import WindowFacts

    width, height = changes.pop("size", (320, 240))
    base = {"surface_created": True, "visible": True, "minimised": False, "width": width,
            "height": height, "scene_graph": True, "exposed": True, "root_width": 320.0,
            "root_height": 240.0}
    return WindowFacts(**{**base, **changes})


def _force(host: Any, *, spent: bool = False, renders_3d: bool = True, **changes: Any) -> None:
    """Put Qt's facts where a real window system would (the offscreen platform cannot:
    it renders no 3D, and nothing collapses or minimises there). The state is a pure
    function of them (``lifecycle.state_of``; its whole truth table is in
    ``tests/unit/test_quick3d_lifecycle_states.py``) — which is what makes the host
    testable here; that Qt's facts DO take these values, and what happens then, is the
    render tier's (``test_quick3d_lifecycle.py``)."""
    forced = _facts(**changes)
    host._facts = lambda: forced  # noqa: SLF001
    host._spent = spent  # noqa: SLF001
    if renders_3d:
        host.graphics_api = lambda: "Direct3D11"


#: Facts → the state the host must report, and the words its refusal must contain.
NO_FRAME = [
    ({"scene_graph": False, "exposed": False}, "starting", r"would kill the process.*wait_ready\(\)"),
    ({"scene_graph": False}, "starting", r"would kill the process.*wait_ready\(\)"),
    ({"exposed": False}, "starting", r"shown again but not exposed yet.*wait_ready\(\)"),
    ({"root_width": 0.0}, "starting", r"has not caught up.*scene 0 x 240.*wait_ready\(\)"),
    ({"size": (200, 240)}, "starting", r"has not caught up.*window 200 x 240, scene 320 x 240"),
    ({"visible": False}, "hidden", "not visible"),
    ({"visible": False, "exposed": False}, "hidden", "not visible"),
    ({"minimised": True}, "hidden", "minimised"),
    ({"size": (0, 240)}, "hidden", r"no usable area \(0 x 240 px"),       # the collapsed pane
    ({"size": (1, 240)}, "hidden", r"no usable area \(1 x 240 px.*Below 2 px"),
    ({"size": (320, 1)}, "hidden", r"no usable area \(320 x 1 px"),
]


class TestStateMachine:
    """Review rounds 1 and 2. The state is derived from Qt on every call; no Python flag
    says "shown" (the parent of an embedded container shows, hides, resizes and
    collapses it without asking)."""

    def test_the_host_reads_its_facts_from_qt(self, host) -> None:
        """What the real window says here, under the offscreen platform — the plumbing
        from Qt into ``WindowFacts``. (No fact is a Python flag: each changes because Qt
        changed.)"""
        born = host._facts()  # noqa: SLF001
        assert (born.surface_created, born.visible, born.minimised) == (False, False, False)
        assert (born.width, born.height, born.root_width, born.root_height) == (320, 240, 320.0, 240.0)
        assert host.state.value == "new" and host.is_visible() is False
        host.show()
        assert host._facts().surface_created is True, "Qt created the platform window"  # noqa: SLF001
        assert host.wait_ready(5.0) and host.state.value == "live"
        shown = host._facts()  # noqa: SLF001
        assert (shown.visible, shown.scene_graph, shown.exposed) == (True, True, True)
        assert host.is_visible() is True
        host._view.resize(200, 100)  # noqa: SLF001
        assert host.wait_ready(5.0)
        resized = host._facts()  # noqa: SLF001
        assert (resized.width, resized.height, resized.root_width, resized.root_height) == (
            200, 100, 200.0, 100.0)
        host.hide()
        assert host._facts().visible is False and host.state.value == "hidden"  # noqa: SLF001

    def test_shown_and_hidden_again_before_any_event_is_not_new(self, host) -> None:
        """Review round 2, P1-A — the red CI job. ``show(); hide()`` with no event in
        between: no scene graph, not visible. The first state machine called that
        ``new`` and took the offscreen frame; on xcb the process segfaulted in the
        next event-loop pass. The platform window EXISTS — the host is hidden, has no
        offscreen frame, and is not spent by the refusal."""
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        host.show()
        host.hide()
        assert host._facts().surface_created is True  # noqa: SLF001
        assert host.state.value == "hidden"
        host.graphics_api = lambda: "Direct3D11"  # past the "no 3D here" check, to the state's
        with pytest.raises(HostStateError, match="not visible"):
            host.grab("after show and hide")
        assert host._spent is False and host.grabs == 0  # noqa: SLF001
        assert host.wait_ready(5.0) is False

    def test_only_a_window_that_never_had_a_platform_window_is_new(self, host) -> None:
        _force(host, surface_created=False, visible=False, exposed=False, scene_graph=False)
        assert host.state.value == "new"
        _force(host, surface_created=True, visible=False, exposed=False, scene_graph=False)
        assert host.state.value == "hidden"

    @pytest.mark.parametrize(("changes", "state", "_words"), NO_FRAME)
    def test_the_state_follows_the_facts(self, host, changes: dict[str, Any], state: str,
                                         _words: str) -> None:
        _force(host)
        assert host.state.value == "live"
        _force(host, **changes)
        assert host.state.value == state
        assert host.is_closed is False and host.is_visible() is changes.get("visible", True)
        _force(host, spent=True, **changes)
        assert host.state.value == "spent"

    @pytest.mark.parametrize(("changes", "_state", "words"), NO_FRAME)
    def test_no_frame_no_pick_no_projection_unless_live(self, host, changes: dict[str, Any],
                                                        _state: str, words: str) -> None:
        """THE guard, in every state that is not live. Shown and grabbed before the first
        expose: a good image and a dead process. A collapsed pane (review round 2,
        P1-B): Qt calls the window visible and exposed, the grab is blank, the pick
        bogus, the projection (0, 0). One pixel wide: no 3D is drawn. Resized a moment
        ago: the scene still has the old size."""
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        _force(host, **changes)
        for call in (lambda: host.grab("no"), lambda: host.pick(1.0, 1.0),
                     lambda: host.project(0.0, 0.0, 0.0)):
            with pytest.raises(HostStateError, match=words):
                call()
        assert host.grabs == 0 and host._spent is False, "a refusal must not spend the host"  # noqa: SLF001

    def test_a_spent_host_refuses_everything_that_would_kill_it(self, host) -> None:
        """One hidden grab, then ``show()`` — what the old message recommended — killed
        the process. A spent host says: build a new one."""
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        _force(host, spent=True, visible=False, exposed=False, scene_graph=False)
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
        _force(host, surface_created=False, visible=False, exposed=False, scene_graph=False)
        with pytest.raises(HostStateError, match="embedded host has no offscreen frame"):
            host.grab("embedded")
        assert host.state.value == "new", "the refusal must not spend the host"

    def test_a_frame_that_is_not_the_windows_size_is_refused(self, host) -> None:
        """The last line of defence behind the state: an image that does not have the
        window's size is not the current scene, whatever the state said."""
        from open_garden_planner.ui.view3d.quick3d.host import EngineError

        host.show()
        assert host.wait_ready(5.0)
        _force(host)  # live, 320 x 240 — and "renders 3D", so that grab() gets as far as Qt
        assert host.grab("right size").width() == round(320 * host.device_pixel_ratio())
        assert host.grabs == 1
        _force(host, size=(100, 80), root_width=100.0, root_height=80.0)  # Qt's window is 320 x 240
        with pytest.raises(EngineError, match="for a 100 x 80 window.*not the current scene"):
            host.grab("wrong size")
        assert host.grabs == 1

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


class TestWaitReady:
    """``wait_ready()`` is True only when a ``grab()`` right after it is truthful; it
    waits only for a host that is on its way, looks again in every pass, and blocks for
    events instead of spinning (review round 2, P2-5)."""

    def test_it_reports_instead_of_hanging(self, host) -> None:
        assert host.wait_ready(5.0) is False, "a host nobody showed is not on its way"
        host.show()
        assert host.wait_ready(5.0) is True and host.state.value == "live"
        host.hide()
        assert host.state.value == "hidden", "hidden at once, whatever Qt still calls exposed"
        assert host.wait_ready(5.0) is False

    @pytest.mark.parametrize("changes", [
        {"visible": False}, {"minimised": True}, {"size": (0, 240)}, {"size": (1, 240)},
        {"surface_created": False, "visible": False},
    ], ids=["hidden", "minimised", "collapsed", "one pixel", "new"])
    def test_it_does_not_wait_for_a_host_that_is_not_on_its_way(self, host,
                                                               changes: dict[str, Any]) -> None:
        _force(host, **changes)
        started = time.perf_counter()
        assert host.wait_ready(5.0) is False
        assert time.perf_counter() - started < 1.0

    def test_it_times_out_without_spinning(self, host) -> None:
        """A window that never starts: False when the time is up — after a few dozen
        looks at Qt, not after the tens of thousands a busy loop takes."""
        forced, looks = _facts(scene_graph=False, exposed=False), [0]

        def facts() -> Any:
            looks[0] += 1
            return forced

        host._facts = facts  # noqa: SLF001
        started = time.perf_counter()
        assert host.wait_ready(0.3) is False and host.state.value == "starting"
        elapsed = time.perf_counter() - started
        assert 0.28 <= elapsed < 2.0
        assert looks[0] < 200, f"{looks[0]} looks at Qt in {elapsed:.2f} s: a busy loop"

    @pytest.mark.parametrize(("later", "expected"), [
        ({}, True),                              # the first expose arrived: live
        ({"visible": False}, False),             # hidden during the wait
        ({"minimised": True}, False),
        ({"size": (0, 240)}, False),             # the pane was collapsed during the wait
    ], ids=["becomes live", "hidden meanwhile", "minimised meanwhile", "collapsed meanwhile"])
    def test_it_looks_again_in_every_pass(self, host, later: dict[str, Any],
                                          expected: bool) -> None:
        """It looked at visibility once and then span to its timeout (3.0 of 3.0 s)."""
        from PyQt6.QtCore import QTimer

        _force(host, scene_graph=False, exposed=False)
        QTimer.singleShot(40, lambda: _force(host, **later))
        started = time.perf_counter()
        assert host.wait_ready(5.0) is expected
        assert time.perf_counter() - started < 1.5

    def test_it_comes_back_when_the_host_is_closed_or_destroyed_during_the_wait(
            self, qtbot) -> None:  # noqa: ARG002
        from PyQt6 import sip
        from PyQt6.QtCore import QTimer

        for kill in (lambda made: made.close(), lambda made: sip.delete(made._view)):  # noqa: SLF001
            made = _new_host()
            forced = _facts(scene_graph=False, exposed=False)
            real = made._facts  # noqa: SLF001
            made._facts = lambda forced=forced, real=real: forced if real() is not None else None  # noqa: SLF001
            QTimer.singleShot(40, lambda made=made, kill=kill: kill(made))
            started = time.perf_counter()
            assert made.wait_ready(5.0) is False
            assert time.perf_counter() - started < 1.5 and made.is_closed
            made.close()

    def test_it_gives_up_as_soon_as_qt_reports_no_graphics_device(self, host) -> None:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtQuick import QQuickWindow

        _force(host, scene_graph=False, exposed=False)
        QTimer.singleShot(40, lambda: host._view.sceneGraphError.emit(  # noqa: SLF001
            QQuickWindow.SceneGraphError.ContextNotAvailable, "no device today"))
        started = time.perf_counter()
        assert host.wait_ready(5.0) is False
        assert time.perf_counter() - started < 1.5
        assert host.health().scene_graph_error == "no device today"


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

    def test_render_records_refuses_beside_a_shown_quick_window(self, host) -> None:
        """Review round 2, P1-C. ``render_records`` records Qt's messages with a
        process-wide Python handler; a SHOWN Qt Quick window renders on a thread of its
        own, and the two deadlock the process. It refuses — before it installs
        anything."""
        from PyQt6.QtCore import qInstallMessageHandler

        from open_garden_planner.ui.view3d.quick3d.host import shown_quick_windows
        from open_garden_planner.ui.view3d.quick3d.offscreen import RenderError, render_records

        def mine(*_args: object) -> None:
            return None

        def render() -> None:
            render_records({"a": _record("a")}, shot=TopDownShot((0.0, 0.0), 0.5), qml_dir=_qml_dir())

        assert shown_quick_windows() == 0
        host.show()
        assert host.wait_ready(5.0) and shown_quick_windows() == 1
        before = qInstallMessageHandler(mine)
        try:
            with pytest.raises(RenderError, match=r"1 Qt Quick window\(s\) are shown.*deadlocks"):
                render()
            host.hide()
            assert shown_quick_windows() == 0
            with pytest.raises(RenderError, match="Software backend"):  # past the refusal now
                render()
        finally:
            after = qInstallMessageHandler(before)
        assert after is mine

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
        assert set(lifecycle.HOST_CALLS) == public - {"is_visible", "health"}

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

    def test_an_engine_call_that_fails_mid_transaction_is_recovered_by_reset(self, host) -> None:
        """The L1.1 recipe for a sink on the REAL host (no pixels here; the render tier
        looks at them): the engine refuses the second Model of a transaction, the sync
        is poisoned, ``reset()`` empties the engine through ``Quick3DSink.clear()`` —
        also the Model the failed call left behind — and after ``apply`` the engine
        holds what ``sync.expected_state()`` says."""
        from open_garden_planner.core.scene3d import SyncStateError, engine_pose
        from open_garden_planner.ui.view3d.quick3d.host import EngineError

        sink = Quick3DSink(host)
        sync = SceneSync(sink, validate=True)
        sun, ground = SunState(40.0, 180.0), GroundSpec(800.0, 600.0)
        sync.apply({"a": _record("a"), "b": _record("b", 300.0, colour=BLUE)}, sun=sun, ground=ground)
        after = {"a": _record("a", 50.0, 20.0, 30.0), "b": _record("b", 300.0, colour=BLUE),
                 "c": _record("c", -300.0), "d": _record("d", 0.0, 400.0)}

        real_create, calls = host.create_part, [0]

        def refusing(spec: Any, pose: Any, tint: Any) -> object:
            calls[0] += 1
            if calls[0] == 2:
                raise EngineError("injected: the engine refused this Model")
            return real_create(spec, pose, tint)

        host.create_part = refusing
        with pytest.raises(EngineError, match="injected"):
            sync.apply(after)
        del host.create_part
        assert sync.poisoned and sink.in_transaction
        assert sorted(p.item_id for p in host.engine_parts()) == ["a", "b", "c"]  # half applied
        with pytest.raises(SyncStateError):
            sync.apply(after)

        sync.reset()
        assert not sync.poisoned and not sink.in_transaction
        assert host.engine_parts() == [] and sink.item_ids() == ()
        assert host.scene_property("sunUp") is False and host.scene_property("groundVisible") is False

        sync.apply(after, sun=sun, ground=ground)
        expected = sync.expected_state()
        assert sink.state() == expected
        want = sorted((item_id, index, engine_pose(held.transform).position,
                       held.material.tint_rgba[:3])
                      for item_id, held in expected.items.items()
                      for index, part in enumerate(held.parts) if part.mesh.vertex_count)
        have = sorted((p.item_id, p.part_index, p.position, p.tint_rgb) for p in host.engine_parts())
        assert have == want and len(have) == 4
        assert host.scene_property("sunUp") is True and host.scene_property("groundVisible") is True

    def test_close_destroys_every_model_and_is_idempotent(self, qtbot) -> None:  # noqa: ARG002
        made = _new_host()
        sink = Quick3DSink(made)
        SceneSync(sink).apply({"a": _record("a"), "b": _record("b", 200.0)})
        assert len(made.engine_parts()) == 2 and not made.is_closed
        made.close()
        assert made.is_closed
        made.close()  # a second close is a no-op, not an error on a deleted view


def _failing_destroy_copy(tmp_path: Path) -> Path:
    """The scene with a switch: ``failDestroy`` makes ``destroyPart()`` throw — an engine
    that REFUSES to destroy a Model, on demand."""
    return _broken_copy(
        tmp_path, "    function destroyPart(part) {\n",
        "    property bool failDestroy: false\n"
        "    function destroyPart(part) {\n"
        "        if (failDestroy)\n"
        "            throw new Error(\"injected: the engine refuses to destroy this Model\")\n")


class TestTeardownIsExceptionSafe:
    """Review round 2, P2-3. With a ``destroyPart`` that fails, every ``reset()`` forgot
    one more part and the FOURTH "succeeded" with three Models left in the engine and
    the host's books empty. Now a Model leaves the books only when the engine confirms
    it; teardown finishes whatever fails; and it can be tried again."""

    @pytest.fixture()
    def refusing(self, qtbot, tmp_path: Path) -> Iterator[Any]:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

        made = Quick3DHost((64, 48), qml_dir=_failing_destroy_copy(tmp_path))
        yield made
        if not made.is_closed:
            assert made._root.setProperty("failDestroy", False)  # noqa: SLF001
        made.close()

    @staticmethod
    def _refuse(made: Any, on: bool) -> None:
        assert made._root.setProperty("failDestroy", on)  # noqa: SLF001

    def test_reset_never_succeeds_while_the_engine_keeps_its_models(self, refusing) -> None:
        from open_garden_planner.core.scene3d import SyncStateError
        from open_garden_planner.ui.view3d.quick3d.host import EngineError

        sink = Quick3DSink(refusing)
        sync = SceneSync(sink)
        records = {name: _record(name, 200.0 * k) for k, name in enumerate("abc")}
        sync.apply(records, sun=SunState(40.0, 180.0), ground=GroundSpec(500.0, 500.0))
        self._refuse(refusing, True)
        for attempt in range(1, 6):  # the fourth used to "succeed"
            with pytest.raises(EngineError, match=r"clear\(\): 3 of 5 steps failed.*still holds 3 Model"):
                sync.reset()
            assert sync.poisoned, f"reset() {attempt} must not report success"
            assert refusing.part_count() == 3 == len(refusing.engine_parts()), (
                f"after reset() {attempt}: books and engine must agree")
        # the teardown FINISHED what it could: the sun is off and the ground is gone
        assert refusing.scene_property("sunUp") is False
        assert refusing.scene_property("groundVisible") is False
        assert sink.item_ids() == () and not sink.in_transaction
        with pytest.raises(SyncStateError):
            sync.apply(records)
        self._refuse(refusing, False)  # the engine recovers: the next reset() goes through
        sync.reset()
        assert not sync.poisoned and refusing.part_count() == 0 == len(refusing.engine_parts())
        sync.apply(records)
        assert sink.state() == sync.expected_state()
        assert sorted(p.item_id for p in refusing.engine_parts()) == ["a", "b", "c"]

    def test_a_sink_whose_clear_failed_builds_nothing_on_the_leftovers(self, refusing) -> None:
        """Its books are empty and the engine is not: ``begin()`` refuses until a
        ``clear()`` went through."""
        from open_garden_planner.core.scene3d import SinkProtocolError
        from open_garden_planner.ui.view3d.quick3d.host import EngineError

        sink = Quick3DSink(refusing)
        SceneSync(sink).apply({"a": _record("a")})
        self._refuse(refusing, True)
        with pytest.raises(EngineError):
            sink.clear()
        assert sink.item_ids() == () and refusing.part_count() == 1
        with pytest.raises(SinkProtocolError, match=r"the last clear\(\) failed"):
            sink.begin()
        with pytest.raises(SinkProtocolError, match="already holds 1 Model"):
            Quick3DSink(refusing)  # and nobody else gets the scene either
        self._refuse(refusing, False)
        sink.clear()
        sink.begin()
        sink.commit()
        assert refusing.part_count() == 0

    def test_a_model_the_engine_did_not_destroy_stays_a_live_part(self, refusing) -> None:
        from open_garden_planner.core.scene3d import engine_pose
        from open_garden_planner.ui.view3d.quick3d.host import EngineError

        pose = engine_pose(Transform(east_cm=70.0))
        buffers = engine_buffers(prism_mesh([SQUARE], 10.0))
        handle = refusing.create_part(PartSpec("a", 0, "vc", buffers, True), pose, RED)
        self._refuse(refusing, True)
        with pytest.raises(EngineError, match=r"destroyPart\(\) did not retire the Model"):
            refusing.destroy_part(handle)
        assert handle.model is not None and handle.geometry is not None, "the buffers it draws stay"
        refusing.pose_part(handle, pose)  # still a live part of this host
        assert refusing.part_count() == 1 == len(refusing.engine_parts())
        assert any("injected" in line for line in refusing.health().qml_warnings)
        self._refuse(refusing, False)
        refusing.destroy_part(handle)
        assert refusing.part_count() == 0 and handle.model is None

    def test_a_scene_without_the_destroy_function_is_an_error_not_a_forgotten_model(
            self, qtbot, tmp_path: Path) -> None:  # noqa: ARG002
        from open_garden_planner.core.scene3d import engine_pose
        from open_garden_planner.ui.view3d.quick3d.host import EngineError, Quick3DHost

        made = Quick3DHost((64, 48), qml_dir=_broken_copy(
            tmp_path, "    function destroyPart(part) {", "    function destroyPartRenamed(part) {"))
        try:
            buffers = engine_buffers(prism_mesh([SQUARE], 10.0))
            handle = made.create_part(PartSpec("a", 0, "vc", buffers, True),
                                      engine_pose(Transform()), RED)
            with pytest.raises(EngineError, match=r"no function destroyPart\(\)"):
                made.destroy_part(handle)
            with pytest.raises(EngineError, match=r"clear\(\): 1 of 3 steps failed"):
                made.clear()
            assert made.part_count() == 1 == len(made.engine_parts())
        finally:
            made.close()
        assert made.is_closed, "close() goes through: the Model dies with the view"

    def test_close_goes_through_when_a_model_cannot_be_destroyed(
            self, refusing, caplog: pytest.LogCaptureFixture) -> None:
        from open_garden_planner.ui.view3d.quick3d import host as host_module

        SceneSync(Quick3DSink(refusing)).apply({"a": _record("a"), "b": _record("b", 200.0)})
        self._refuse(refusing, True)
        with caplog.at_level(logging.WARNING, logger=host_module.__name__):
            refusing.close()
        assert refusing.is_closed and refusing.state.value == "closed"
        assert sum("could not be destroyed" in r.getMessage() for r in caplog.records) == 2
        refusing.close()  # and again: a no-op

    def test_a_close_that_raised_can_be_called_again(self, qtbot) -> None:  # noqa: ARG002
        """``close()`` used to set its flag first: a failure half way left a host that
        called itself closed with its window still there. The flag is set last."""
        made = _new_host()
        SceneSync(Quick3DSink(made)).apply({"a": _record("a")})
        view = made._view  # noqa: SLF001

        def refuse() -> None:
            raise RuntimeError("injected: hide() failed")

        view.hide = refuse
        with pytest.raises(RuntimeError, match="injected"):
            made.close()
        assert made.is_closed is False and made.state.value != "closed"
        assert made.part_count() == 0, "the Models went before the window was touched"
        del view.hide
        made.close()
        assert made.is_closed


class TestOneSinkPerScene:
    """Review round 2, P2-4: ``attach()`` accepted a host that already held Models — two
    owners of one scene, and a teardown that destroys the other's Models. Refused."""

    def test_a_sink_refuses_a_scene_that_already_holds_models(self, host, qtbot) -> None:  # noqa: ARG002
        from open_garden_planner.core.scene3d import SinkProtocolError

        first = Quick3DSink(host)
        SceneSync(first).apply({"a": _record("a")})
        with pytest.raises(SinkProtocolError, match=r"Quick3DSink\(\): .* already holds 1 Model"):
            Quick3DSink(host)
        other = _new_host()
        try:
            second = Quick3DSink(other)
            with pytest.raises(SinkProtocolError, match=r"attach\(\): .* already holds 1 Model"):
                second.attach(host)
            assert second.scene is other, "a refused attach changes nothing"
            first.clear()
            second.attach(host)  # empty now: free to take
            assert second.scene is host
        finally:
            other.close()

    def test_a_closed_host_cannot_be_given_to_a_sink(self, qtbot) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        made = _new_host()
        made.close()
        with pytest.raises(HostStateError, match="closed"):
            Quick3DSink(made)


class TestSceneFunctionsThatFail:
    """A QML function that THROWS returns nothing to Python — silently; the QML engine
    only warns. "No hit", "(0, 0)" and "no parts" must never mean "could not look"."""

    @pytest.mark.parametrize(("function", "call"), [
        ("pickAt(x, y)", lambda h: h.pick(5.0, 5.0)),
        ("projectToView(x, y, z)", lambda h: h.project(0.0, 0.0, 0.0)),
        ("listParts()", lambda h: h.engine_parts()),
    ], ids=["pick", "project", "engine_parts"])
    def test_a_throwing_function_is_an_engine_error(self, qtbot, tmp_path: Path, function: str,  # noqa: ARG002
                                                    call: Any) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import EngineError, Quick3DHost

        name = function.split("(")[0]
        made = Quick3DHost((64, 48), qml_dir=_broken_copy(
            tmp_path, f"    function {function} {{\n",
            f"    function {function} {{\n        throw new Error(\"injected: {name} broke\")\n"))
        try:
            _force(made, size=(64, 48), root_width=64.0, root_height=48.0)
            with pytest.raises(EngineError, match=rf"{name}\(\) returned nothing — it failed"):
                call(made)
            assert any(f"injected: {name} broke" in line for line in made.health().qml_warnings)
        finally:
            made.close()

    def test_a_missing_function_is_an_engine_error(self, qtbot, tmp_path: Path) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import EngineError, Quick3DHost

        made = Quick3DHost((64, 48), qml_dir=_broken_copy(
            tmp_path, "    function listParts() {", "    function listPartsRenamed() {"))
        try:
            with pytest.raises(EngineError, match=r"no function listParts\(\)"):
                made.engine_parts()
        finally:
            made.close()


class TestHealth:
    """Review round 2, P1-C: what a SHOWN host learns about itself — on the GUI thread,
    from Qt's own signals. There is no Qt message recorder for a shown host: a Python
    handler beside Qt's render thread deadlocks the process (the render tier's control:
    ``test_a_python_message_handler_beside_a_shown_window_deadlocks``)."""

    def test_a_healthy_host(self, host, qtbot) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import HostHealth

        assert host.health() == HostHealth(0, (), 0, None) and host.health().ok
        host.show()
        assert host.wait_ready(5.0)
        qtbot.waitUntil(lambda: host.health().frames_presented >= 1, timeout=5000)
        assert host.health().ok and host.slot_calls_off_the_gui_thread == 0
        host.close()
        assert host.health().frames_presented >= 1, "health() answers on a closed host"

    def test_the_host_installs_no_qt_message_handler_and_offers_no_watch(self, host) -> None:
        """Structural: the removed API stays removed, in the host and in its module."""
        from open_garden_planner.ui.view3d.quick3d import host as host_module
        from open_garden_planner.ui.view3d.quick3d import qt_messages

        source = Path(host_module.__file__).read_text(encoding="utf-8")
        assert "qInstallMessageHandler" not in source and "import qt_messages" not in source
        assert not hasattr(host, "watch_messages") and not hasattr(host, "engine_errors")
        assert not hasattr(qt_messages, "logging_sink")

    def test_qt_sees_a_slot_on_scene_graph_error(self, host) -> None:
        """With NOTHING connected Qt answers a failed graphics initialisation with a modal
        error box and then aborts the process (the render tier's control). Qt decides by
        asking whether the signal is connected — this asks the same question."""
        view = host._view  # noqa: SLF001
        assert view.receivers(view.sceneGraphError) >= 1
        assert view.receivers(view.frameSwapped) >= 1

    def test_a_graphics_device_that_cannot_be_created_is_reported_and_logged(
            self, host, qtbot, caplog: pytest.LogCaptureFixture) -> None:
        from PyQt6.QtQuick import QQuickWindow

        from open_garden_planner.ui.view3d.quick3d import host as host_module

        with caplog.at_level(logging.ERROR, logger=host_module.__name__):
            host._view.sceneGraphError.emit(  # noqa: SLF001
                QQuickWindow.SceneGraphError.ContextNotAvailable,
                "Failed to initialize graphics backend for D3D11.")
            assert host.health().scene_graph_error is None, "queued: delivered by the event loop"
            qtbot.waitUntil(lambda: host.health().scene_graph_error is not None, timeout=5000)
        report = host.health()
        assert report.scene_graph_error == "Failed to initialize graphics backend for D3D11."
        assert report.ok is False
        assert [(r.levelno, r.name) for r in caplog.records] == [(logging.ERROR, host_module.__name__)]
        assert "graphics device could not be created" in caplog.records[0].getMessage()
        assert host_module.__name__.startswith("open_garden_planner."), "a child of the app's logger"

    def test_a_qml_warning_is_reported_and_logged(self, qtbot, tmp_path: Path,  # noqa: ARG002
                                                  caplog: pytest.LogCaptureFixture) -> None:
        """A binding that fails at run time raises nothing anywhere; the QML engine's
        ``warnings`` signal carries it — file and line included."""
        from open_garden_planner.ui.view3d.quick3d import host as host_module

        marker = "    property real windTime: 0\n"
        broken = _broken_copy(tmp_path, marker, marker + "    property real probe: nothingHere.value\n")
        with caplog.at_level(logging.WARNING, logger=host_module.__name__):
            made = host_module.Quick3DHost((64, 48), qml_dir=broken)
        try:
            report = made.health()
            assert report.ok is False and report.qml_warning_count == len(report.qml_warnings) >= 1
            assert re.search(r"Garden\.qml:\d+.*nothingHere", report.qml_warnings[0])
            assert any(r.levelno == logging.WARNING and "nothingHere" in r.getMessage()
                       for r in caplog.records)
        finally:
            made.close()

    def test_a_flood_of_qml_warnings_is_counted_not_kept(self, host) -> None:
        from PyQt6.QtQml import QQmlError

        from open_garden_planner.ui.view3d.quick3d import host as host_module

        def error(k: int) -> QQmlError:
            made = QQmlError()
            made.setDescription(f"injected warning {k}")
            return made

        engine = host._view.engine()  # noqa: SLF001
        engine.warnings.emit([error(k) for k in range(host_module._KEEP_WARNINGS + 25)])  # noqa: SLF001
        report = host.health()
        assert report.qml_warning_count == host_module._KEEP_WARNINGS + 25  # noqa: SLF001
        assert len(report.qml_warnings) == host_module._KEEP_WARNINGS  # noqa: SLF001
        assert "injected warning 0" in report.qml_warnings[0]


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
    env = _child_env(tmp_path)
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
