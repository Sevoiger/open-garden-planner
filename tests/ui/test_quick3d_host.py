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
- the host's state rules, and that a broken scene file is a loud error.

What it cannot check — that any of it is DRAWN correctly — is the render tier
(``tests/integration/test_quick3d_render.py``).

A canary runs first, in a child process: if Qt Quick 3D objects cannot be
created on this platform at all, the module skips with the child's output
instead of taking the test process down with it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

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

SRC = Path(__file__).resolve().parents[2] / "src"
SQUARE = ((-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0))
RED = (230, 20, 20, 255)
BLUE = (20, 40, 230, 255)

_CANARY = (
    "import sys\n"
    "from PyQt6.QtWidgets import QApplication\n"
    "app = QApplication(sys.argv[:1])\n"
    "from open_garden_planner.core.scene3d import (Material, Params, Record, SceneSync,\n"
    "                                               Transform)\n"
    "from open_garden_planner.ui.view3d.quick3d.adapter import Quick3DSink\n"
    "from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost\n"
    "host = Quick3DHost((64, 48))\n"
    "ring = ((-5.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-5.0, 5.0))\n"
    "SceneSync(Quick3DSink(host)).apply({'a': Record(item_id='a', kind='SHED', shape='rectangle',\n"
    "    footprints=(ring,), transform=Transform(), material=Material('SHED'), height_cm=10.0,\n"
    "    params=Params({'seed': 'a'}))})\n"
    "assert len(host.engine_parts()) == 1\n"
    "host.grab('canary')\n"
    "host.close()\n"
    "app.processEvents()\n"
    # … and everything else the tests below do to a window: show, embed, grab, hide
    "from PyQt6.QtWidgets import QWidget\n"
    "shown = Quick3DHost((64, 48))\n"
    "shown.show(); app.processEvents(); shown.wait_exposed(2.0); shown.grab('shown'); shown.hide()\n"
    "shown.close()\n"
    "parent = QWidget()\n"
    "embedded = Quick3DHost((64, 48))\n"
    "embedded.container(parent)\n"
    "parent.show(); embedded.show(); app.processEvents(); embedded.hide(); embedded.close()\n"
    "app.processEvents()\n"
    "print('canary ok', flush=True)\n"
)


@pytest.fixture(scope="module", autouse=True)
def quick3d_objects_can_be_created() -> None:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SRC), env.get("PYTHONPATH")) if p)
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own code
        [sys.executable, "-c", _CANARY], env=env, capture_output=True, text=True, timeout=170)
    if proc.returncode != 0 or "canary ok" not in proc.stdout:
        pytest.skip("Qt Quick 3D objects cannot be created under the offscreen platform here "
                    f"(exit {proc.returncode}): {proc.stderr[-800:]}")


def _record(item_id: str, east: float = 0.0, north: float = 0.0, rotation: float = 0.0,
            colour: tuple[int, int, int, int] = RED, height: float | None = 100.0) -> Record:
    return Record(item_id=item_id, kind="SHED", shape="rectangle", footprints=(SQUARE,),
                  transform=Transform(east_cm=east, north_cm=north, rotation_deg=rotation),
                  material=Material("SHED", fill_rgba=colour), height_cm=height,
                  params=Params({"seed": item_id}))


@pytest.fixture()
def host(qtbot):  # noqa: ARG001 - Qt must be initialised
    from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

    made = Quick3DHost((320, 240))
    yield made
    made.close()


def _uploads() -> int:
    from open_garden_planner.ui.view3d.quick3d.geometry import BufferGeometry

    return int(BufferGeometry.uploads)


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
        from open_garden_planner.ui.view3d.quick3d.host import QML_DIR, EngineError, Quick3DHost

        broken = tmp_path / "qml"
        shutil.copytree(QML_DIR, broken)
        scene = broken / "Garden.qml"
        text = scene.read_text(encoding="utf-8")
        assert "            castsShadow: true\n" in text
        scene.write_text(text.replace("            castsShadow: true\n",
                                      "            castsShadows: true\n"), encoding="utf-8")
        with pytest.raises(EngineError, match=r"Garden\.qml did not load.*castsShadows"):
            Quick3DHost((64, 48), qml_dir=broken)


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


class TestStateRules:
    def test_a_never_shown_host_grabs_once_and_never_picks(self, host) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        SceneSync(Quick3DSink(host)).apply({"a": _record("a")})
        with pytest.raises(HostStateError, match="rendered a frame"):
            host.pick(10.0, 10.0)
        image = host.grab("first")
        assert (image.width(), image.height()) == (round(320 * host.device_pixel_ratio()),
                                                   round(240 * host.device_pixel_ratio()))
        assert host.grabs == 1
        with pytest.raises(HostStateError, match="ONE frame"):
            host.grab("second")
        with pytest.raises(HostStateError, match="shown host"):
            host.pick(10.0, 10.0)

    def test_a_host_that_was_shown_and_is_hidden_does_not_grab(self, host, qtbot) -> None:
        from open_garden_planner.ui.view3d.quick3d.host import HostStateError

        host.show()
        qtbot.waitUntil(host.is_visible, timeout=5000)
        host.wait_exposed(2.0)
        host.request_frame()
        host.grab("shown")
        assert host.pick(5.0, 5.0) is None  # nothing is drawn here; the call itself is legal
        assert len(host.project(0.0, 0.0, 0.0)) == 2
        host.hide()
        assert not host.is_visible()
        with pytest.raises(HostStateError, match="hidden"):
            host.grab("hidden")

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
        host.hide()
        assert not container.isVisible()

    def test_the_host_survives_the_death_of_the_widget_that_embedded_it(self, qtbot) -> None:  # noqa: ARG002
        """A window container belongs to its parent widget: when that widget is destroyed
        Qt destroys the view with it. The host must notice (``is_closed``) and ``close()``
        must then be a quiet release — it first raised "wrapped C/C++ object of type
        QQuickView has been deleted" in this file's own teardown."""
        from PyQt6 import sip
        from PyQt6.QtWidgets import QWidget

        from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

        made = Quick3DHost((64, 48))
        SceneSync(Quick3DSink(made)).apply({"a": _record("a")})
        parent = QWidget()
        made.container(parent)
        assert not made.is_closed
        sip.delete(parent)  # what closing the main window does to the workspace
        assert made.is_closed
        made.close()
        made.close()

    def test_close_destroys_every_model_and_is_idempotent(self, qtbot) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost

        made = Quick3DHost((64, 48))
        sink = Quick3DSink(made)
        SceneSync(sink).apply({"a": _record("a"), "b": _record("b", 200.0)})
        assert len(made.engine_parts()) == 2 and not made.is_closed
        made.close()
        assert made.is_closed and made.engine_parts() == []  # the Models went before the view
        made.close()  # a second close is a no-op, not an error on a deleted view


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


class TestOffscreenWithoutA3dBackend:
    def test_render_records_runs_the_whole_path_and_reports_the_backend(self, qtbot) -> None:  # noqa: ARG002
        """No pixels here (the software scene graph draws no 3D) — but the path runs end
        to end: host, sink, sync, grab, close, and the report says ``Software``, which
        is how a caller knows the frame is not a 3D frame."""
        from open_garden_planner.ui.view3d.quick3d.offscreen import image_rgba, render_records

        frame = render_records({"a": _record("a"), "flat": _record("flat", height=None)},
                               shot=TopDownShot((0.0, 0.0), 0.5), sun=SunState(45.0, 225.0),
                               ground=GroundSpec(1000.0, 1000.0), size=(160, 120))
        assert (frame.items, frame.parts) == (2, 1)
        assert frame.graphics_api == "Software" and frame.adapter is None
        assert frame.open_ms > 0.0 and set(frame.breakdown_ms) == {"qml_load", "scene_apply", "grab"}
        assert frame.qt["counts"]["error"] == 0
        pixels = frame.rgba()
        assert pixels.shape == (round(120 * frame.device_pixel_ratio),
                                round(160 * frame.device_pixel_ratio), 4)
        assert (image_rgba(frame.image) == pixels).all()

    def test_a_broken_scene_file_fails_the_render(self, qtbot, tmp_path: Path) -> None:  # noqa: ARG002
        from open_garden_planner.ui.view3d.quick3d.host import QML_DIR, EngineError
        from open_garden_planner.ui.view3d.quick3d.offscreen import render_records

        broken = tmp_path / "qml"
        shutil.copytree(QML_DIR, broken)
        scene = broken / "Garden.qml"
        scene.write_text(scene.read_text(encoding="utf-8").replace(
            "import QtQuick3D.Helpers\n", "import QtQuick3D.Helpers\nimport NoSuchModule\n"),
            encoding="utf-8")
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
