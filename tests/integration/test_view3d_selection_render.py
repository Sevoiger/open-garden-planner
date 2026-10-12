"""Selection, picking and walking on the real renderer (Phase 17 L1.8 — the render tier).

Opt-in (``OGP_RENDER3D=1``): a driver process with a real window system
(``view3d_selection_driver.py``) drives the real engine; the tests here only JUDGE
what it saved, with a camera model, a projection and a ray caster of their own
(``view3d_selection_support.py``, ``core/scene3d/pick_oracle.py``) — never with the
engine's. Gates G1 (20/20 picks) and G4 (the ring is where the footprint is), and
the render rows of G2, G7 and G8.

Run it on the GPU and on WARP::

    OGP_RENDER3D=1 venv/Scripts/python.exe -m pytest tests/integration/test_view3d_selection_render.py -s
    OGP_RENDER3D=1 QSG_RHI_PREFER_SOFTWARE_RENDERER=1 venv/Scripts/python.exe -m pytest <the same>

Measured on the dev box (RTX 4070, Direct3D 11) and on WARP, 2026-10-12 — the numbers
are next to each bound; ``-s`` prints them again.
"""

from __future__ import annotations

import colorsys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from open_garden_planner.core.scene3d import point_in_ring, pose_ring
from open_garden_planner.core.scene3d.selection import (
    MITRE_LIMIT,
    RING_LIFT_CM,
    RING_WIDTH_PX,
    SELECTION_RGB,
)
from open_garden_planner.ui.view3d.quick3d.shots import PerspectiveShot
from tests.integration import view3d_selection_driver as driver
from tests.integration.quick3d_render_support import failure_report, load_rgb, render_tier
from tests.integration.view3d_selection_support import (
    distances_to,
    mask_pixels,
    named_pick_points,
    oracle_scene,
    outline_pixels,
    perspective_outline,
    ring_mask,
    robust_answer,
    run_selection_driver,
    top_down_outline,
)

pytestmark = render_tier

Run = tuple[dict[str, Any], Path]
SCENARIOS = "ring,ring_depth,pick"


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    out = tmp_path_factory.mktemp("view3d_selection")
    proc, result = run_selection_driver(out, SCENARIOS)
    assert proc.returncode == 0 and result.get("status") == "ok", failure_report(proc, out)
    return result, out


def _frame(run: Run, info: dict[str, Any]) -> tuple[np.ndarray, float]:
    return load_rgb(run[1] / info["file"]), float(info["dpr"])


def test_the_run_drew_with_no_qt_error_and_names_its_device(run: Run) -> None:
    """The ring's shader is one more shipped shader: a compile error is only a Qt
    WARNING and the frame still renders — so the judge reads Qt's messages."""
    messages = run[0]["qt_messages"]
    assert messages["counts"]["error"] == 0, messages["errors"]
    assert messages["counts"]["warning"] == 0, messages["warnings"]
    assert run[0]["adapter"], "Qt named the graphics adapter"
    print(f"\n[adapter] {run[0]['adapter']}")


# ── gate G4: the ring is where the footprint is ──────────────────────────────

#: The band is RING_WIDTH_PX wide, outside the outline; a mitred square corner reaches
#: sqrt(2) widths; one pixel more for rasterisation and anti-aliasing.
RING_REACH_PX = RING_WIDTH_PX * 2 ** 0.5 + 1.0


def _ring_step(run: Run, name: str) -> tuple[np.ndarray, float, dict[str, Any]]:
    step = next(s for s in run[0]["scenarios"]["ring"]["steps"] if s["name"] == name)
    rgb, dpr = _frame(run, step)
    return rgb, dpr, step


def test_with_nothing_selected_there_is_no_ring(run: Run) -> None:
    for name in ("none", "cleared"):
        rgb, _dpr, step = _ring_step(run, name)
        assert int(ring_mask(rgb).sum()) == 0, name
        assert step["after"]["ring_visible"] is False


@pytest.mark.parametrize("item_id", driver.RING_SELECTED)
def test_the_ring_lies_on_the_footprint_outline_seen_from_above(run: Run, item_id: str) -> None:
    """Gate G4, per shape: a turned rectangle, a polygon with a hole (the hole has its
    own ring), a circle, a plant on a raised bed. Every ring pixel lies within
    ``RING_REACH_PX`` of OUR projection of the record's outline, on its outside; and
    the ring is complete — the whole outline has ring within a band's width."""
    rgb, dpr, _step = _ring_step(run, item_id)
    record = driver.ring_records()[item_id]
    size = (rgb.shape[1], rgb.shape[0])
    outline = outline_pixels(top_down_outline(record, driver.RING_TOP, size, dpr))
    ring = mask_pixels(ring_mask(rgb))
    assert len(ring) > 100, "there is a ring"
    off = distances_to(ring, outline)
    covered = distances_to(outline, ring)
    reach = RING_REACH_PX * dpr
    complete = float((covered <= (RING_WIDTH_PX + 1.0) * dpr).mean())
    # outside the solid: the centre of a ring pixel is not in the footprint (outer minus holes)
    inside = 0
    for x, y in ring:
        east = driver.RING_TOP.center[0] + (x - size[0] / 2.0) / (driver.RING_TOP.px_per_cm * dpr)
        north = driver.RING_TOP.center[1] - (y - size[1] / 2.0) / (driver.RING_TOP.px_per_cm * dpr)
        for outer, holes in record.regions:
            if point_in_ring(east, north, pose_ring(record.transform, outer)) and not any(
                    point_in_ring(east, north, pose_ring(record.transform, hole)) for hole in holes):
                inside += 1
    print(f"\n[ring {item_id}] pixels {len(ring)}, farthest from the outline {off.max():.2f} px "
          f"(bound {reach:.2f}), mean {off.mean():.2f}; outline covered {complete:.1%}; "
          f"pixel centres inside the footprint {inside / len(ring):.1%}")
    assert float(off.max()) <= reach
    assert complete >= 0.98
    assert inside / len(ring) <= 0.12, "the band lies OUTSIDE the footprint (edge pixels aside)"
    # … and it is a thin band, not a filled shape: about one band width per outline pixel
    perimeter = len(outline) * 0.5
    assert 0.6 * perimeter * RING_WIDTH_PX * dpr <= len(ring) <= 1.6 * perimeter * RING_WIDTH_PX * dpr


def test_every_selected_item_has_its_ring_in_one_frame_and_the_others_none(run: Run) -> None:
    rgb, dpr, step = _ring_step(run, "all")
    size = (rgb.shape[1], rgb.shape[0])
    records = driver.ring_records()
    ring = mask_pixels(ring_mask(rgb))
    selected = np.concatenate([outline_pixels(top_down_outline(records[i], driver.RING_TOP, size, dpr))
                               for i in driver.RING_SELECTED])
    assert float(distances_to(ring, selected).max()) <= RING_REACH_PX * dpr
    assert float((distances_to(selected, ring) <= (RING_WIDTH_PX + 1.0) * dpr).mean()) >= 0.98
    for item_id in ("other", "bed"):   # not selected: nothing blue along their outlines
        outline = outline_pixels(top_down_outline(records[item_id], driver.RING_TOP, size, dpr))
        near = distances_to(outline, ring) <= 2.0 * dpr
        # the plant's ring crosses nothing of the bed's outline; the shed is far from all
        assert float(near.mean()) <= 0.02, item_id
    assert step["ids"] == list(driver.RING_SELECTED) and step["sent"] is True


def test_the_ring_of_a_plant_on_a_bed_is_at_the_beds_soil_height(run: Run) -> None:
    """Gate G4, the height: in a perspective look the ring sits where OUR camera model
    puts the plant's outline at the soil's height (base + lift) — and NOT where it
    would be on the ground, 38 cm lower."""
    info = run[0]["scenarios"]["ring"]["perspective"]
    rgb, dpr = _frame(run, info)
    none, _ = _frame(run, run[0]["scenarios"]["ring"]["perspective_none"])
    assert int(ring_mask(none).sum()) == 0, "nothing else in that frame is ring-coloured"
    record = driver.ring_records()["plant"]
    view = tuple(run[0]["scenarios"]["ring"]["view_size"])
    ring = mask_pixels(ring_mask(rgb))
    assert len(ring) > 60
    soil = outline_pixels(perspective_outline(record, driver.RING_PERSPECTIVE, view,
                                              driver.BED_SOIL + RING_LIFT_CM, dpr))
    ground = outline_pixels(perspective_outline(record, driver.RING_PERSPECTIVE, view,
                                                RING_LIFT_CM, dpr))
    at_soil, at_ground = distances_to(ring, soil), distances_to(ring, ground)
    print(f"\n[ring height] pixels {len(ring)}; from the outline at soil height: mean "
          f"{at_soil.mean():.2f} px, max {at_soil.max():.2f}; from the outline on the ground: "
          f"mean {at_ground.mean():.2f} px")
    assert float(at_soil.max()) <= (RING_WIDTH_PX * MITRE_LIMIT + 1.0) * dpr
    assert float(at_soil.mean()) <= RING_WIDTH_PX * dpr
    assert float(at_ground.mean()) >= 4.0 * float(at_soil.mean()) and float(at_ground.mean()) > 8.0 * dpr
    # the ring is complete although the plant stands in the middle of it: it is drawn in front
    assert float((distances_to(soil, ring) <= (RING_WIDTH_PX + 1.5) * dpr).mean()) >= 0.97


def test_the_ring_is_the_canvas_selection_blue_on_screen(run: Run) -> None:
    """The colour a user sees (perspective view: exposure and the filmic curve applied)
    keeps the hue of the 2D selection colour; in the truth view it is that colour's
    linear value, as every pixel there is."""
    rgb, _dpr = _frame(run, run[0]["scenarios"]["ring"]["perspective"])
    seen = rgb[ring_mask(rgb)].astype(np.float64)
    core = seen[seen[:, 2] >= np.percentile(seen[:, 2], 50)].mean(axis=0)   # not the AA fringe
    hue = colorsys.rgb_to_hsv(*(core / 255.0))[0] * 360.0
    wanted = colorsys.rgb_to_hsv(*(np.array(SELECTION_RGB) / 255.0))[0] * 360.0
    print(f"\n[ring colour] on screen rgb({core[0]:.0f}, {core[1]:.0f}, {core[2]:.0f}), hue "
          f"{hue:.1f} deg; the canvas's {SELECTION_RGB}, hue {wanted:.1f} deg")
    assert abs(hue - wanted) <= 10.0
    assert core[2] > 150 and core[2] > core[0] + 80, "it reads as a saturated blue"


def test_a_selection_change_creates_and_destroys_no_engine_object(run: Run) -> None:
    """THE memory rule of the ring (ADR-065): one geometry, made with the first
    selection, re-loaded ever after; the Models and their buffers are not touched."""
    scenario = run[0]["scenarios"]["ring"]
    steps = scenario["steps"]
    first = steps[0]["before"]
    assert first["ring_geometries"] == 0 and first["ring_loads"] == 0
    for step in steps:
        for moment in ("before", "after"):
            assert step[moment]["parts"] == first["parts"] == step[moment]["engine_parts"], step["name"]
            assert step[moment]["model_uploads"] == first["model_uploads"], step["name"]
        loaded = step["after"]["ring_loads"] - step["before"]["ring_loads"]
        assert loaded == (1 if step["ids"] else 0), step["name"]
        assert step["after"]["ring_geometries"] == (1 if step["after"]["ring_loads"] else 0)
        assert step["sink_geometry_uploads"] == steps[0]["sink_geometry_uploads"]
    steady = scenario["steady"]
    assert steady["after"]["ring_loads"] - steady["before"]["ring_loads"] == 20
    assert steady["after"]["ring_geometries"] == steady["before"]["ring_geometries"] == 1
    assert steady["after"]["parts"] == steady["before"]["parts"] == steady["after"]["engine_parts"]
    assert steady["after"]["model_uploads"] == steady["before"]["model_uploads"]
    assert scenario["state_equals_expected"] is True


def test_the_ring_follows_a_moved_item_and_the_move_uploads_no_model_geometry(run: Run) -> None:
    info = run[0]["scenarios"]["ring"]["moved"]
    rgb, dpr = _frame(run, info)
    size = (rgb.shape[1], rgb.shape[0])
    moved = driver.record("box", "GARAGE_SHED", -300.0, 150.0, driver.rect(220.0, 140.0),
                          height=120.0, colour=driver.RED, rotation=70.0, shape="rectangle")
    ring = mask_pixels(ring_mask(rgb))
    here = outline_pixels(top_down_outline(moved, driver.RING_TOP, size, dpr))
    before = outline_pixels(top_down_outline(driver.ring_records()["box"], driver.RING_TOP, size, dpr))
    assert float(distances_to(ring, here).max()) <= RING_REACH_PX * dpr
    assert float((distances_to(before, ring) <= 2.0 * dpr).mean()) < 0.2, "not where the item was"
    assert info["after"]["model_uploads"] == info["before"]["model_uploads"]
    assert info["after"]["ring_loads"] == info["before"]["ring_loads"] + 1
    assert info["after"]["parts"] == info["before"]["parts"]


def test_a_depth_tested_ring_would_be_hidden_in_a_walls_own_masonry(run: Run) -> None:
    """WHY the ring is drawn in front (the control of that decision, measured): a wall
    is built 30 cm thick on a 5 cm stroke. Seen from above, the shipped ring shows the
    whole outline; the same ring depth-tested lies inside the wall and all but vanishes."""
    scenario = run[0]["scenarios"]["ring_depth"]
    record = driver.wall_record()
    counts = {}
    for name in ("in_front", "depth_tested"):
        rgb, dpr = _frame(run, scenario[name])
        size = (rgb.shape[1], rgb.shape[0])
        outline = outline_pixels(top_down_outline(record, driver.RING_TOP, size, dpr))
        ring = mask_pixels(ring_mask(rgb))
        counts[name] = (len(ring), float((distances_to(outline, ring) <= 4.0 * dpr).mean()))
    print(f"\n[ring depth] in front: {counts['in_front'][0]} px, outline covered "
          f"{counts['in_front'][1]:.1%}; depth-tested: {counts['depth_tested'][0]} px, "
          f"{counts['depth_tested'][1]:.1%}")
    assert scenario["shipped_scale"] == pytest.approx(0.01)
    assert counts["in_front"][1] >= 0.98
    assert counts["depth_tested"][1] <= 0.15 and counts["depth_tested"][0] < counts["in_front"][0] / 10


# ── gate G1: twenty of twenty picks ──────────────────────────────────────────

#: Of the twenty seeded positions at most this many may be "nothing" (the ground, the
#: sky): a list of twenty lawn clicks would prove nothing about picking.
NOTHING_QUOTA = 6


def _pick_case(run: Run, plan: str) -> dict[str, Any]:
    data = run[0]["scenarios"]["pick"][plan]
    scene, _view, _manager = driver.load_plan(plan)
    records = driver.plan_records(scene)
    shot = PerspectiveShot(tuple(data["shot"]["eye"]), tuple(data["shot"]["target"]),
                           float(data["shot"]["fov"]))
    view = (float(data["view_size"][0]), float(data["view_size"][1]))
    assert view == (float(driver.PICK_VIEW[0]), float(driver.PICK_VIEW[1])), "the window was clamped"
    assert shot == driver.pick_camera(float(scene.width_cm), float(scene.height_cm),
                                      driver.plan_top(records))
    assert data["build_failures"] == [] and data["records"] == len(records)
    engine = {tuple(point): (hit["id"] if hit else None)
              for point, hit in zip(data["points"], data["hits"], strict=True)}
    return {"records": records, "shot": shot, "view": view, "engine": engine,
            "oracle": oracle_scene(records), "data": data}


@pytest.fixture(scope="module")
def pick_cases(run: Run, qapp: Any) -> dict[str, dict[str, Any]]:  # noqa: ARG001 - Qt for the scene
    return {plan: _pick_case(run, plan) for plan in driver.PICK_PLANS}


@pytest.mark.parametrize("plan", list(driver.PICK_PLANS))
def test_twenty_of_twenty_picks_agree_with_the_cpu_oracle(pick_cases, plan: str) -> None:
    """Gate G1. Twenty seeded view positions per plan, taken in seeded order: the first
    twenty at which the oracle's answer does not hang on a silhouette edge, at most
    ``NOTHING_QUOTA`` of them on the ground or the sky. At each, the engine's
    ``pick`` names what the oracle's ray meets first in the records' own meshes."""
    case = pick_cases[plan]
    chosen: list[tuple[tuple[float, float], str | None]] = []
    nothing = 0
    for point in driver.seeded_points():
        answer, robust = robust_answer(case["oracle"], case["shot"], case["view"], *point)
        if not robust or (answer is None and nothing >= NOTHING_QUOTA):
            continue
        nothing += answer is None
        chosen.append((point, answer))
        if len(chosen) == 20:
            break
    assert len(chosen) == 20, "the seeded list holds twenty judgeable positions"
    agree = [point for point, answer in chosen if case["engine"][point] == answer]
    kinds = sorted({case["records"][answer].kind for _point, answer in chosen if answer})
    print(f"\n[pick {plan}] {len(agree)}/20; kinds hit: {kinds}; nothing: {nothing}")
    assert len(agree) == 20, [(point, answer, case["engine"][point]) for point, answer in chosen
                              if case["engine"][point] != answer]
    assert len(kinds) >= (5 if plan == "bench_small" else 2), "the twenty are not one item"


def test_the_named_positions_are_picked_as_the_oracle_says(pick_cases) -> None:
    """Gate G1's three named cases: a point on a plant standing on a raised bed, a
    point inside a polygon's hole (the ground, or what stands there — never the item
    around it), a point on the sky."""
    seen: dict[str, int] = {}
    for plan, case in pick_cases.items():
        named = named_pick_points(plan, case["records"], case["shot"], case["view"])
        for label, point in named:
            answer, robust = robust_answer(case["oracle"], case["shot"], case["view"], *point)
            assert robust, (plan, label)
            assert point in case["engine"], "the driver clicked where the judge points"
            assert case["engine"][point] == answer, (plan, label, point)
            seen[label] = seen.get(label, 0) + 1
            if label == "plant_on_bed":
                record = case["records"][answer]
                assert record.kind in ("TREE", "SHRUB", "PERENNIAL") and record.transform.base_cm > 0.0
            elif label == "sky":
                assert answer is None
            print(f"\n[pick {plan}] {label} at {point}: "
                  f"{case['records'][answer].kind if answer else 'nothing'}")
    assert seen.get("plant_on_bed", 0) >= 2 and seen.get("sky", 0) >= 2 and seen.get("hole", 0) >= 1


@pytest.mark.parametrize("plan", list(driver.PICK_PLANS))
def test_every_judgeable_seeded_position_agrees(pick_cases, plan: str) -> None:
    """Beyond the twenty: ALL 400 seeded positions of the plan. Off the silhouette
    edges the engine and the oracle agree everywhere (measured: 362 of 362 and 365 of
    365 on the GPU); ON an edge either answer is right, and both are items or nothing
    the neighbouring rays see."""
    case = pick_cases[plan]
    agree = disagree = edge = edge_agree = 0
    for point in driver.seeded_points():
        answer, robust = robust_answer(case["oracle"], case["shot"], case["view"], *point)
        got = case["engine"][point]
        if robust:
            agree += got == answer
            disagree += got != answer
        else:
            edge += 1
            edge_agree += got == answer
    print(f"\n[pick {plan}] off the edges {agree}/{agree + disagree} agree; on an edge "
          f"{edge_agree}/{edge}; {case['data']['ms_per_pick']} ms a pick; "
          f"{case['oracle'].triangle_count} triangles, {case['data']['models']} Models")
    assert disagree == 0 and agree >= 300
    assert case["data"]["ms_per_pick"] < 5.0
