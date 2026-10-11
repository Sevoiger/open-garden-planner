"""The engine package on a real renderer (Phase 17 L1.2, #385 — gates G5–G9).

Render tier: opt-in (``OGP_RENDER3D=1``), because 3D needs a real window system
and the default test run is ``QT_QPA_PLATFORM=offscreen``. One driver process
(``quick3d_render_driver.py``, real QPA) renders every scenario; the tests here
only JUDGE its frames and answers, with a CPU projection of their own
(``shots.top_down_pixel`` + ``core.scene3d.pose_ring``) — never with the
engine's. Assertions are about MEANING (where a box is, which face is lit, what
a click hits), never pixel-exact goldens, so they hold on a GPU, on WARP and on
llvmpipe alike. How to run it: ``quick3d_render_support.py``.

Measured on the L1.2 dev box (RTX 4070, Direct3D 11) and on WARP, 2026-10-11 —
the numbers are next to each bound.
"""

from __future__ import annotations

import math
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from open_garden_planner.core.scene3d import Record, Transform, srgb8_to_linear, srgb_to_linear
from open_garden_planner.core.scene3d import sun_direction_scene as sun_direction
from open_garden_planner.core.shadow_geometry import compute_scene_shadows, shadow_direction_scene
from open_garden_planner.ui.view3d.quick3d.host import GRAPHICS_API_NAMES, RENDERING_APIS
from open_garden_planner.ui.view3d.quick3d.look import NEUTRAL_DAY
from tests.integration import quick3d_render_driver as driver
from tests.integration.quick3d_render_support import (
    QML_SOURCE,
    QT_ERRORS_EXIT,
    blue_mask,
    centroid,
    failure_report,
    footprint_mask,
    iou,
    load_rgb,
    luma,
    red_mask,
    render_env,
    render_tier,
    run_driver,
    scene_polygon_mask,
    yellow_mask,
)

pytestmark = render_tier

Run = tuple[dict[str, Any], Path]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    out = tmp_path_factory.mktemp("quick3d_render")
    proc, result = run_driver(out, "truth,materials,rest,states,edits,pick,sun")
    assert proc.returncode == 0 and result.get("status") == "ok", failure_report(proc, out)
    return result, out


def test_no_builder_failed_in_any_offscreen_frame(run: Run) -> None:
    """``render_records`` contains a failed builder — the item is then in the frame
    WITHOUT a solid — and names it in ``build_failures``. A render test that never
    looked would judge a frame with a hole in it."""
    frames = 0
    for name in ("truth", "materials", "rest"):
        for key, frame in run[0]["scenarios"][name].items():
            if isinstance(frame, dict) and "build_failures" in frame:
                assert frame["build_failures"] == {}, (name, key)
                frames += 1
    assert frames >= 8, "the check must have looked at the offscreen frames"


def _frame(run: Run, info: dict[str, Any]) -> tuple[np.ndarray, float]:
    return load_rgb(run[1] / info["file"]), float(info["dpr"])


def _moved(record: Record, east: float, north: float, rotation: float | None = None) -> Record:
    from dataclasses import replace

    turn = record.transform.rotation_deg if rotation is None else rotation
    return replace(record, transform=Transform(east_cm=east, north_cm=north, rotation_deg=turn))


# ── G5: frame mapping, measured in pixels ────────────────────────────────────


def test_east_is_right_and_north_is_up(run: Run) -> None:
    """A red box EAST of the origin and a blue box NORTH of it, seen from straight
    above: each lands on the pixels our own projection predicts (measured: centroids
    exact to 0.0 px, IoU 1.000 on Direct3D 11 and on WARP)."""
    rgb, dpr = _frame(run, run[0]["scenarios"]["truth"]["boxes"])
    height, width = rgb.shape[:2]
    records = driver.truth_records()
    red, blue = red_mask(rgb), blue_mask(rgb)
    assert iou(red, footprint_mask(records["east"], driver.TOP, rgb.shape, dpr)) >= 0.95
    assert iou(blue, footprint_mask(records["north"], driver.TOP, rgb.shape, dpr)) >= 0.95
    red_x, red_y = centroid(red)
    blue_x, blue_y = centroid(blue)
    assert red_x > width / 2 + 100 * dpr and abs(red_y - height / 2) < 1.0   # east → right
    assert blue_y < height / 2 - 80 * dpr and abs(blue_x - width / 2) < 1.0  # north → up


@pytest.mark.parametrize("wrong", ["mirrored east-west", "mirrored north-south", "axes swapped"])
def test_a_mirrored_or_swapped_mapping_would_fail(run: Run, wrong: str) -> None:
    """The gate's own control: predict the frame under each WRONG mapping — none of
    them may match what was rendered (measured: IoU 0.000 for all three)."""
    rgb, dpr = _frame(run, run[0]["scenarios"]["truth"]["boxes"])
    records = driver.truth_records()

    def mapped(record: Record) -> Record:
        east, north = record.transform.east_cm, record.transform.north_cm
        target = {"mirrored east-west": (-east, north), "mirrored north-south": (east, -north),
                  "axes swapped": (north, east)}[wrong]
        return _moved(record, *target)

    red = iou(red_mask(rgb), footprint_mask(mapped(records["east"]), driver.TOP, rgb.shape, dpr))
    blue = iou(blue_mask(rgb), footprint_mask(mapped(records["north"]), driver.TOP, rgb.shape, dpr))
    assert min(red, blue) < 0.05, (wrong, red, blue)


@pytest.mark.parametrize("rotation", driver.BAR_ROTATIONS)
def test_a_turned_non_square_box_lands_where_the_cpu_predicts(run: Run, rotation: float) -> None:
    """The proof L1.1 handed over: ``engine_pose`` gives the Model ``eulerRotation.y =
    +rotation_deg``, and that IS a counter-clockwise turn seen from above in the
    engine (measured IoU: 1.000, 0.9994, 1.000, 0.9895; long axis within 0.03°)."""
    rgb, dpr = _frame(run, run[0]["scenarios"]["truth"][f"bar_{rotation:g}"])
    mask = red_mask(rgb)
    assert iou(mask, footprint_mask(driver.bar_record(rotation), driver.TOP, rgb.shape, dpr)) >= 0.95
    rows, cols = np.nonzero(mask)
    x, y = cols - cols.mean(), -(rows - rows.mean())  # image rows grow downwards: north is −row
    axis = 0.5 * math.degrees(math.atan2(2.0 * float((x * y).mean()),
                                         float((x * x).mean() - (y * y).mean())))
    off = (axis - rotation + 90.0) % 180.0 - 90.0  # a bar's axis is defined modulo 180°
    assert abs(off) < 1.0, (rotation, axis)


@pytest.mark.parametrize("rotation", [17.0, 213.5])
def test_the_wrong_rotation_sense_would_fail(run: Run, rotation: float) -> None:
    """Control: a Model turned by −rotation does not match (measured IoU 0.288 at 17°,
    0.157 at 213.5°). 0° and 90° cannot tell the two senses apart for a rectangle —
    which is why the gate has two angles that can."""
    rgb, dpr = _frame(run, run[0]["scenarios"]["truth"][f"bar_{rotation:g}"])
    wrong = driver.bar_record((-rotation) % 360.0)
    assert iou(red_mask(rgb), footprint_mask(wrong, driver.TOP, rgb.shape, dpr)) < 0.5


# ── at rest, the wind moves nothing ──────────────────────────────────────────


def _rest_offsets(rgb: np.ndarray, dpr: float) -> dict[str, tuple[float, float]]:
    """Per wind shader: (IoU with the CPU footprint, distance of the centroids in px)."""
    records = driver.rest_records()
    out: dict[str, tuple[float, float]] = {}
    for kind, mask in (("foliage", red_mask(rgb)), ("grass", blue_mask(rgb))):
        predicted = footprint_mask(records[kind], driver.TOP, rgb.shape, dpr)
        (x, y), (px, py) = centroid(mask), centroid(predicted)
        out[kind] = (iou(mask, predicted), math.hypot(x - px, y - py))
    return out


def test_at_rest_a_vertex_with_full_wind_weight_is_where_its_mesh_puts_it(run: Run) -> None:
    """The scene's clock stands at 0 until L1.4 animates it. Two boxes whose EVERY
    vertex has the full wind weight (uv.x = 1), in a wind 40 x the look's: each is
    drawn exactly on the pixels the CPU predicts from its mesh (measured: IoU 1.000,
    offset 0.00 px, Direct3D 11 and WARP). Heights, spreads, picks and shadows are
    all measured against that mesh — a shader that moved it would make them wrong."""
    rgb, dpr = _frame(run, run[0]["scenarios"]["rest"]["top"])
    for kind, (overlap, offset) in _rest_offsets(rgb, dpr).items():
        assert overlap >= 0.97, (kind, overlap)
        assert offset < 0.5, (kind, offset)


def test_a_shader_that_sways_at_rest_would_fail(tmp_path: Path) -> None:
    """Control: the shaders as they were before the fix (the ``* rest`` factor removed).
    The same boxes land 10.2 px (foliage) and 18.9 px (grass) from where their meshes
    are — 20 cm and 38 cm in the garden — with no error and no warning anywhere."""
    qml = tmp_path / "qml"
    shutil.copytree(QML_SOURCE, qml)
    for file in ("foliage.vert", "grass.vert"):
        text = (qml / file).read_text(encoding="utf-8")
        assert text.count(" * rest;") == 2, f"the control no longer finds the rest factor in {file}"
        (qml / file).write_text(text.replace(" * rest;", ";"), encoding="utf-8")
    out = tmp_path / "out"
    proc, result = run_driver(out, "rest", qml_dir=qml, env=render_env(tmp_path))
    assert proc.returncode == 0 and result.get("status") == "ok", failure_report(proc, out)
    info = result["scenarios"]["rest"]["top"]
    for kind, (overlap, offset) in _rest_offsets(load_rgb(out / info["file"]), float(info["dpr"])).items():
        assert offset > 5.0 * float(info["dpr"]), (kind, offset)
        assert overlap < 0.8, (kind, overlap)


# ── G6: every sink call, in pixels ───────────────────────────────────────────


def _steps(run: Run) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in run[0]["scenarios"]["edits"]["steps"]}


def test_adding_items_shows_them(run: Run) -> None:
    step = _steps(run)["0_add"]
    rgb, dpr = _frame(run, step)
    assert iou(red_mask(rgb), footprint_mask(driver.EDIT_A, driver.TOP, rgb.shape, dpr)) >= 0.95
    assert iou(blue_mask(rgb), footprint_mask(driver.EDIT_B, driver.TOP, rgb.shape, dpr)) >= 0.95
    assert step["uploads_delta"] == 2 and step["sink_uploads"] == 2
    assert {part["id"] for part in step["engine_parts"]} == {"a", "b"}
    assert step["picks"]["a"]["hit"] == "a" and step["picks"]["b"]["hit"] == "b"


def test_update_transform_moves_the_pixels_and_uploads_nothing(run: Run) -> None:
    steps = _steps(run)
    step = steps["1_move"]
    rgb, dpr = _frame(run, step)
    assert iou(red_mask(rgb), footprint_mask(driver.EDIT_A_MOVED, driver.TOP, rgb.shape, dpr)) >= 0.95
    assert iou(red_mask(rgb), footprint_mask(driver.EDIT_A, driver.TOP, rgb.shape, dpr)) == 0.0
    assert step["uploads_delta"] == 0, "a move must not upload geometry"
    assert step["build_count"] == steps["0_add"]["build_count"], "a move must not run a builder"
    assert step["picks"]["a"]["hit"] == "a" and step["picks"]["old"]["hit"] is None
    (part,) = [p for p in step["engine_parts"] if p["id"] == "a"]
    assert part["position"] == pytest.approx([-250.0, 0.0, 150.0]) and part["yaw"] == 30.0


def test_update_material_recolours_and_uploads_nothing(run: Run) -> None:
    steps = _steps(run)
    step = steps["2_recolour"]
    rgb, dpr = _frame(run, step)
    assert int(red_mask(rgb).sum()) == 0
    assert iou(yellow_mask(rgb), footprint_mask(driver.EDIT_A_YELLOW, driver.TOP, rgb.shape, dpr)) >= 0.95
    assert step["uploads_delta"] == 0, "a recolour must not upload geometry"
    assert step["build_count"] == steps["0_add"]["build_count"]
    (part,) = [p for p in step["engine_parts"] if p["id"] == "a"]
    assert part["tint"] == list(driver.YELLOW[:3])


def test_replace_geometry_changes_the_silhouette(run: Run) -> None:
    step = _steps(run)["3_replace"]
    rgb, dpr = _frame(run, step)
    yellow = yellow_mask(rgb)
    assert iou(yellow, footprint_mask(driver.EDIT_A_WIDE, driver.TOP, rgb.shape, dpr)) >= 0.95
    assert iou(yellow, footprint_mask(driver.EDIT_A_YELLOW, driver.TOP, rgb.shape, dpr)) < 0.5
    assert step["uploads_delta"] == 1
    assert step["picks"]["a"]["hit"] == "a"


def test_remove_clears_the_item(run: Run) -> None:
    step = _steps(run)["4_remove"]
    rgb, dpr = _frame(run, step)
    assert int(yellow_mask(rgb).sum()) == 0 and int(red_mask(rgb).sum()) == 0
    assert iou(blue_mask(rgb), footprint_mask(driver.EDIT_B, driver.TOP, rgb.shape, dpr)) >= 0.95
    assert [part["id"] for part in step["engine_parts"]] == ["b"]
    assert step["picks"]["gone"]["hit"] is None and step["picks"]["b"]["hit"] == "b"
    assert step["uploads_delta"] == 0


def test_an_item_removed_and_added_again_renders_and_picks(run: Run) -> None:
    """The re-attach trap (``ogp-3d-renderer`` §2): the spike measured a blank frame and
    0/20 picks when a geometry outlived its Model. Here the re-added item is back,
    pixel for pixel (measured mean abs difference 0.0), and it picks."""
    steps = _steps(run)
    step = steps["5_readd"]
    before, _ = _frame(run, steps["3_replace"])
    after, _ = _frame(run, step)
    assert float(np.abs(after - before).mean()) < 0.5
    assert step["uploads_delta"] == 1, "a re-added item gets FRESH geometry"
    assert step["picks"]["a"]["hit"] == "a" and step["picks"]["b"]["hit"] == "b"
    assert step["picks"]["a"]["point"] == pytest.approx([-250.0, -150.0, 80.0], abs=0.5)


def test_clear_then_the_same_items_again_renders_and_picks(run: Run) -> None:
    """``EngineSink.clear()`` — what ``SceneSync.reset()`` calls: the engine holds
    nothing, then everything is sent again and comes back identical."""
    edits = run[0]["scenarios"]["edits"]
    steps = _steps(run)
    cleared, _ = _frame(run, edits["cleared"])
    assert edits["cleared"]["engine_parts"] == []
    assert int((red_mask(cleared) | blue_mask(cleared) | yellow_mask(cleared)).sum()) == 0
    step = steps["6_clear_readd"]
    before, _ = _frame(run, steps["5_readd"])
    after, _ = _frame(run, step)
    assert float(np.abs(after - before).mean()) < 0.5
    assert step["uploads_delta"] == 2
    assert step["picks"]["a"]["hit"] == "a" and step["picks"]["b"]["hit"] == "b"


def test_hide_and_show_brings_the_same_frame_back(run: Run) -> None:
    """The host is kept alive and hidden / shown, never rebuilt (ADR-048 entry 6)."""
    steps = _steps(run)
    before, _ = _frame(run, steps["6_clear_readd"])
    after, _ = _frame(run, steps["7_reentry"])
    assert float(np.abs(after - before).mean()) < 0.5
    assert steps["7_reentry"]["uploads_delta"] == 0
    assert steps["7_reentry"]["picks"]["a"]["hit"] == "a"


# ── G7: the sun ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("azimuth", driver.SUN_AZIMUTHS)
def test_the_shadow_falls_where_the_2d_shadow_does(run: Run, azimuth: float) -> None:
    """Sanity of the light's direction (the full IoU gate over the looks is L1.4): the
    engine's shadow of a 100 x 100 x 200 cm box points along
    ``shadow_direction_scene`` and overlaps the analytic 2D shadow (measured at
    35° elevation, 480 x 360: IoU 0.976–0.978, direction off by ≤ 0.02°)."""
    row = run[0]["scenarios"]["sun"]["azimuths"][f"{azimuth:g}"]
    rgb, dpr = _frame(run, row["top"])
    footprint = footprint_mask(driver.caster_record(), driver.TOP, rgb.shape, dpr)
    brightness = luma(rgb)
    lit = float(np.median(brightness[~footprint]))
    shadow = (brightness < 0.6 * lit) & ~footprint
    ring = list(driver.rect_ring(driver.CASTER_SIDE, driver.CASTER_SIDE))
    analytic = np.zeros_like(shadow)
    for polygon in compute_scene_shadows([(ring, driver.CASTER_HEIGHT)], driver.SUN_ELEVATION, azimuth):
        analytic ^= scene_polygon_mask(polygon, driver.TOP, rgb.shape, dpr)  # odd-even, as in 2D
    analytic &= ~footprint
    assert iou(shadow, analytic) >= 0.90
    centre_x, centre_y = centroid(shadow)
    height, width = rgb.shape[:2]
    east, north = centre_x - width / 2, -(centre_y - height / 2)
    expected = shadow_direction_scene(azimuth)
    off = math.degrees(math.atan2(north, east) - math.atan2(expected[1], expected[0]))
    assert abs((off + 180.0) % 360.0 - 180.0) < 2.0, (azimuth, east, north)


@pytest.mark.parametrize("azimuth", driver.SUN_AZIMUTHS)
def test_lit_and_shaded_faces_are_on_the_correct_sides(run: Run, azimuth: float) -> None:
    """Four cameras, one on each wall's normal: the walls facing the sun are lit, the
    others are in the fill (measured luma 229.5 lit vs 126.6 shaded, every azimuth)."""
    row = run[0]["scenarios"]["sun"]["azimuths"][f"{azimuth:g}"]
    sun = sun_direction(driver.SUN_ELEVATION, azimuth)
    lit, shaded = [], []
    for face, (nx, ny) in driver.FACE_NORMALS.items():
        rgb, _ = _frame(run, row["faces"][face])
        height, width = rgb.shape[:2]
        patch = luma(rgb[height // 2 - 10:height // 2 + 10, width // 2 - 10:width // 2 + 10])
        (lit if nx * sun[0] + ny * sun[1] > 0 else shaded).append((face, float(patch.mean())))
    assert len(lit) == 2 and len(shaded) == 2, (lit, shaded)
    assert min(v for _, v in lit) > 1.4 * max(v for _, v in shaded), (azimuth, lit, shaded)


def test_each_side_camera_looks_at_the_face_it_claims(run: Run) -> None:
    """What makes the lit/shaded test mean something: the pick at each view's centre
    is on that wall, in SCENE coordinates — so camera placement and pick results are
    mapped consistently with the geometry."""
    half, mid = driver.CASTER_SIDE / 2.0, driver.CASTER_HEIGHT / 2.0
    for row in run[0]["scenarios"]["sun"]["azimuths"].values():
        for face, (nx, ny) in driver.FACE_NORMALS.items():
            pick = row["faces"][face]["centre_pick"]
            assert pick is not None and pick["id"] == "caster", (face, pick)
            assert pick["point"] == pytest.approx([nx * half, ny * half, mid], abs=0.5), face


# ── G8: picking ──────────────────────────────────────────────────────────────


def _clicks(run: Run) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(the CPU oracle's plan, what the engine answered) per click — the plan is
    recomputed HERE, in the test process, from the scene definition."""
    plan = driver.pick_plan(driver.pick_records())
    clicks = run[0]["scenarios"]["pick"]["clicks"]
    assert len(plan) == len(clicks)
    for target, click in zip(plan, clicks, strict=True):
        assert (target["x"], target["y"], target["kind"]) == (click["x"], click["y"], click["kind"])
    return list(zip(plan, clicks, strict=True))


def test_pick_ten_of_ten_against_the_cpu_oracle(run: Run) -> None:
    easy = [(target, click) for target, click in _clicks(run) if target["kind"] == "easy"]
    assert len(easy) == 10, "every item of the scene must be reachable from above"
    for target, click in easy:
        assert click["hit"] == target["expected"], (target, click)
        assert click["point"][:2] == pytest.approx([target["x"], target["y"]], abs=1.0)
        assert click["point"][2] == pytest.approx(target["z"], abs=0.5)  # the TOP surface


def test_adversarial_points_are_judged_by_the_mesh_not_the_box(run: Run) -> None:
    """Inside an item's bounding box but off its mesh: a turned bar's corner, the notch
    of the L, the post standing on the deck. A box picker fails every one of them."""
    adversarial = [(t, c) for t, c in _clicks(run) if t["kind"] == "adversarial"]
    assert len(adversarial) >= 5
    assert {t["expected"] for t, _ in adversarial} >= {None, "tall-post", "pillar"}
    for target, click in adversarial:
        assert click["hit"] == target["expected"], (target, click)
        assert click["hit"] != target["in_box_of"]


def test_no_false_hit_on_empty_ground(run: Run) -> None:
    empty = [(t, c) for t, c in _clicks(run) if t["kind"] == "empty"]
    assert len(empty) == 10
    assert [click["hit"] for _, click in empty] == [None] * 10


def test_our_projection_agrees_with_the_engines(run: Run) -> None:
    """The clicks are aimed with ``shots.top_down_pixel``; the engine's own
    ``mapFrom3DScene`` puts the same points within half a pixel (measured 0.0 px)."""
    for _, click in _clicks(run):
        assert click["engine_pixel"] == pytest.approx(click["pixel"], abs=0.5)


# ── G9: errors are loud; a clean run is silent ───────────────────────────────


def test_a_clean_run_records_no_qt_warning(run: Run) -> None:
    """Zero Qt errors AND zero Qt warnings over the whole run — every scenario, every
    hidden render. (``environment`` messages are the machine's, listed in
    ``qt_messages._ENVIRONMENT``; they are reported, not failures.)"""
    result = run[0]
    counts = result["qt_messages"]["counts"]
    assert counts["error"] == 0 and counts["warning"] == 0, result["qt_messages"]
    for name, scenario in result["scenarios"].items():
        assert scenario["qt_delta"]["error"] == 0 and scenario["qt_delta"]["warning"] == 0, name
    for group in ("truth", "materials"):
        for key, frame in result["scenarios"][group].items():
            if isinstance(frame, dict) and "qt" in frame:
                assert frame["qt"]["counts"]["error"] == 0, (group, key)
                assert frame["qt"]["counts"]["warning"] == 0, (group, key)


def _swatch(rgb: np.ndarray, kind: str, dpr: float) -> np.ndarray:
    """Mean colour of the middle of one material swatch's top face."""
    from open_garden_planner.ui.view3d.quick3d.shots import top_down_pixel

    record = driver.swatch_records()[kind]
    height, width = rgb.shape[:2]
    x, y = top_down_pixel(record.transform.east_cm, record.transform.north_cm, driver.TOP,
                          (width, height), dpr)
    half = round(10 * dpr)
    return rgb[round(y) - half:round(y) + half, round(x) - half:round(x) + half].reshape(-1, 3).mean(axis=0)


def test_every_material_kind_renders_with_its_tint(run: Run) -> None:
    """All six part materials — three of them custom shaders — draw, and the tint
    reaches each: red-dominant like the item's colour (measured R/G/B: vc and roof
    171/14/7, foliage and grass 169/14/7, water 90/8/4; glass blends at 61/48/9)."""
    info = run[0]["scenarios"]["materials"]["top"]
    rgb, dpr = _frame(run, info)
    assert info["parts"] == 6 and info["items"] == 6
    ground = rgb[20:40, 20:40].reshape(-1, 3).mean(axis=0)
    assert ground[1] > 1.5 * ground[0] and ground[1] > 1.5 * ground[2]  # meadow green
    colours = {kind: _swatch(rgb, kind, dpr) for kind in driver.MATERIAL_KINDS_ORDER}
    for kind in ("vc", "roof", "foliage", "grass", "water"):
        red, green, blue = colours[kind]
        assert red > 60 and red > 4 * green and red > 4 * blue, (kind, colours[kind])
    assert float(np.abs(colours["glass"] - ground).max()) > 15, colours["glass"]  # it blends in
    # the tint rule is ONE rule: a custom shader's uniform and a principled base colour agree
    assert colours["foliage"][0] == pytest.approx(colours["vc"][0], rel=0.05)
    assert colours["grass"][0] == pytest.approx(colours["vc"][0], rel=0.05)


def test_a_lit_top_face_has_the_radiance_the_sun_predicts(run: Run) -> None:
    """Truth, in numbers: in the un-tone-mapped top-down view a horizontal face shows
    ``tint x (sun brightness x sin(elevation) x sun colour + fill)`` in linear light
    — the sun's vector decides the shading, nothing else (predicted red 167.2,
    measured 171: the rest is the material's small specular term)."""
    rgb, dpr = _frame(run, run[0]["scenarios"]["materials"]["top"])
    tint = srgb8_to_linear(driver.SWATCH_TINT)
    sun_colour = srgb_to_linear(NEUTRAL_DAY.sun_color)
    direct = NEUTRAL_DAY.sun_brightness * math.sin(math.radians(driver.SWATCH_SUN.elevation_deg))
    predicted = 255.0 * tint[0] * (direct * float(sun_colour[0]) + NEUTRAL_DAY.fill_linear[0])
    assert predicted < 250, "the probe must not clip"
    assert _swatch(rgb, "vc", dpr)[0] == pytest.approx(predicted, rel=0.06)


def _broken_copy(tmp_path: Path, file: str, old: str, new: str) -> Path:
    qml = tmp_path / "qml"
    shutil.copytree(QML_SOURCE, qml)
    target = qml / file
    text = target.read_text(encoding="utf-8")
    assert text.count(old) >= 1, f"the control no longer finds {old!r} in {file}"
    target.write_text(text.replace(old, new), encoding="utf-8")
    return qml


@pytest.mark.parametrize(("file", "old", "new"), [
    ("water.frag", "waterLuma(LIGHT_COLOR)", "waterLumaRenamed(LIGHT_COLOR)"),
    ("foliage.frag", "vColor.rgb * uTint", "vColor.rgb * uTintMissing"),
    ("foliage.vert", "uWind * w * gust * rest;\n    VERTEX.z",
     "uWindMissing * w * gust * rest;\n    VERTEX.z"),
    ("grass.vert", "vColor = COLOR;", "vColorMissing = COLOR;"),
], ids=["water.frag", "foliage.frag", "foliage.vert", "grass.vert"])
def test_a_broken_shader_fails_the_render(tmp_path: Path, file: str, old: str, new: str) -> None:
    """Positive control. A shader that does not compile is only a Qt WARNING, and the
    frame still renders — in the wrong colours (the spike measured exit 0 and 449
    changed pond pixels). With the message check, the same render FAILS: one control
    per shader file the package ships."""
    qml = _broken_copy(tmp_path, file, old, new)
    out = tmp_path / "out"
    proc, result = run_driver(out, "materials", qml_dir=qml, env=render_env(tmp_path))
    assert proc.returncode == QT_ERRORS_EXIT, failure_report(proc, out)
    assert result["status"] == "error" and result["error"].startswith("RenderError"), result.get("error")
    assert result["qt_messages"]["counts"]["error"] >= 1
    assert any("shader" in line.lower() for line in result["qt_messages"]["errors"])


def test_a_qml_error_fails_the_render(tmp_path: Path) -> None:
    """Positive control for the QML half: the Model/Light property-name trap
    (``castsShadows`` on a Light) is a load error, reported with the file and line."""
    qml = _broken_copy(tmp_path, "Garden.qml", "            castsShadow: true\n",
                       "            castsShadows: true\n")
    out = tmp_path / "out"
    proc, result = run_driver(out, "truth", qml_dir=qml, env=render_env(tmp_path))
    assert proc.returncode != 0, failure_report(proc, out)
    assert result["status"] == "error" and "Garden.qml" in result["error"], result.get("error")
    assert "castsShadows" in result["error"]


# ── the host's state rules ───────────────────────────────────────────────────


def test_the_host_refuses_what_it_cannot_answer_truthfully(run: Run) -> None:
    """A never-shown view renders ONE frame (the second would be the bare background —
    measured mean abs difference 161 on the probe frame) and may not be shown after
    it; a hidden view renders none; a pick without a rendered frame would read as
    "nothing there". The full matrix of call orders: ``test_quick3d_lifecycle.py``."""
    states = run[0]["scenarios"]["states"]
    for key in ("hidden_pick", "second_hidden_grab", "show_after_offscreen_frame",
                "grab_while_hidden", "pick_while_hidden"):
        assert states[key].startswith("HostStateError"), (key, states[key])
    assert (states["state_new"], states["state_after_offscreen_frame"]) == ("new", "spent")
    assert (states["state_shown"], states["state_hidden"]) == ("live", "hidden")
    first, _ = _frame(run, states["first_hidden_grab"])
    assert int(red_mask(first).sum()) > 1000  # the one frame it does render is the scene


def test_the_run_names_its_graphics_device(run: Run) -> None:
    """The API's name comes from Qt's ENUM (``host.GRAPHICS_API_NAMES``), not from a
    list of strings somebody typed: PyQt calls the members ``OpenGL`` but
    ``Direct3D11Rhi``, and a list written on Windows failed the first Linux run."""
    result = run[0]
    assert result["adapter"], "the adapter must be readable from the log (WARP vs GPU)"
    frames = [frame for frame in result["scenarios"]["truth"].values()
              if isinstance(frame, dict) and "graphics_api" in frame]
    assert frames
    for frame in frames:
        assert frame["graphics_api"] in set(GRAPHICS_API_NAMES.values()) & RENDERING_APIS
        assert frame["adapter"] == result["adapter"]
    # what render_env selects where the tier is run: Qt's default on Windows, OpenGL on Linux
    expected = {"win32": "Direct3D11", "linux": "OpenGL"}.get(sys.platform)
    if expected is not None:
        assert {frame["graphics_api"] for frame in frames} == {expected}
