"""The engine-side half of the selection / walk render tier (Phase 17 L1.8) — not a test module.

Started by ``test_view3d_selection_render.py`` with a real QPA (the test process is
``offscreen``, where nothing 3D is drawn)::

    python tests/integration/view3d_selection_driver.py --out <dir> --scenarios ring,pick,…

The division of labour is the engine story's (``quick3d_render_driver.py``, whose
context, log and isolation this file reuses): the driver only DOES — it drives the
real ``Quick3DHost`` / the real ``Workspace``, saves frames and writes raw engine
answers to ``result.json``; the tests JUDGE, with a camera model, a projection and a
ray caster of their own (``core/scene3d/pick_oracle.py``). The scene definitions
below are plain engine-free functions both halves import.

Exit codes: 0 ok · 1 watchdog · 2 an exception · 4 a render that failed on a Qt error.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import platform
import statistics
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if __name__ == "__main__":  # run as a script: make this checkout's code importable
    for entry in (str(REPO / "src"), str(REPO)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

from open_garden_planner.core.scene3d import (  # noqa: E402
    GroundSpec,
    Material,
    Params,
    Record,
    SceneSync,
    SunState,
    Transform,
)
from open_garden_planner.core.scene3d.builders import production_registry  # noqa: E402
from open_garden_planner.core.scene3d.camera import Bounds, CameraPose, frame_pose  # noqa: E402
from open_garden_planner.ui.view3d.quick3d.shots import PerspectiveShot, TopDownShot  # noqa: E402
from tests.integration.quick3d_render_driver import (  # noqa: E402
    QT_ERRORS_EXIT,
    Context,
    _clear_settings,
    _isolate_app_data,
    _isolate_settings,
    _pump,
)

PLANS = REPO / "tests" / "fixtures" / "plans"
JUNE = date(2026, 6, 21)
HIGH_SUN = SunState(elevation_deg=60.0, azimuth_deg=180.0)

# ── scene definitions (engine-free; the tests import them) ───────────────────

RING_VIEW = (640, 480)                                  # logical pixels
RING_TOP = TopDownShot(center=(0.0, 0.0), px_per_cm=0.5)   # 1280 x 960 cm, north up
RING_GROUND = GroundSpec(width_cm=3000.0, depth_cm=3000.0)
RED, YELLOW, GREY, BROWN = (200, 40, 30, 255), (230, 200, 40, 255), (170, 170, 170, 255), (130, 90, 50, 255)
GREEN = (60, 140, 50, 255)


def rect(width: float, depth: float) -> tuple[tuple[float, float], ...]:
    half_w, half_d = width / 2.0, depth / 2.0
    return ((-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d))


def circle(radius: float, corners: int = 24) -> tuple[tuple[float, float], ...]:
    return tuple((round(radius * math.cos(2 * math.pi * k / corners), 7),
                  round(radius * math.sin(2 * math.pi * k / corners), 7)) for k in range(corners))


def record(item_id: str, kind: str, east: float, north: float, ring, *, height: float | None,
           colour=GREY, rotation: float = 0.0, base: float = 0.0, holes: tuple = (),
           shape: str = "polygon", params: Mapping[str, Any] | None = None,
           parent: str | None = None, path=(), width: float | None = None) -> Record:
    return Record(
        item_id=item_id, kind=kind, shape=shape, footprints=(ring,), holes=holes,
        transform=Transform(east, north, rotation, base),
        material=Material(kind, fill_rgba=colour, stroke_rgba=colour), height_cm=height,
        path=path, path_width_cm=width, params=Params({"seed": item_id, **(params or {})}),
        parent_id=parent)


BED_HEIGHT, BED_SOIL = 40.0, 38.0


def ring_records() -> dict[str, Record]:
    """Gate G4's four: a turned rectangle, a polygon with a hole, a circle, and a plant
    standing on a raised bed (at the bed's soil height) — plus one item that is never
    selected. No kind here is drawn blue: what is blue in a frame is the ring."""
    hole = tuple(reversed(rect(140.0, 90.0)))
    return {
        "box": record("box", "GARAGE_SHED", -420.0, 280.0, rect(220.0, 140.0), height=120.0,
                      colour=RED, rotation=25.0, shape="rectangle"),
        "yard": record("yard", "COMPOST_BIN", 330.0, 290.0, rect(260.0, 200.0), height=60.0,
                       colour=YELLOW, holes=((hole,),)),
        "disc": record("disc", "RAIN_BARREL", -380.0, -260.0, circle(110.0), height=90.0,
                       colour=GREY, shape="circle", params={"radius_cm": 110.0}),
        "bed": record("bed", "RAISED_BED", 300.0, -250.0, rect(360.0, 200.0), height=BED_HEIGHT,
                      colour=BROWN, shape="rectangle"),
        "plant": record("plant", "PERENNIAL", 360.0, -230.0, circle(45.0), height=70.0,
                        colour=GREEN, shape="circle", base=BED_SOIL, parent="bed",
                        params={"radius_cm": 45.0, "plant_category": "PERENNIAL"}),
        "other": record("other", "TOOL_SHED", -40.0, 20.0, rect(120.0, 120.0), height=100.0,
                        colour=RED, shape="rectangle"),
    }


RING_SELECTED = ("box", "yard", "disc", "plant")
#: The perspective look at the plant on its bed: from the south-west, 35 degrees down.
RING_PERSPECTIVE = PerspectiveShot(eye=(-150.0, -820.0, 480.0), target=(340.0, -240.0, 40.0))

WALL_LENGTH, WALL_STROKE = 500.0, 5.0


def wall_record(item_id: str = "wall", east: float = 0.0, north: float = 0.0,
                length: float = WALL_LENGTH, height: float = 180.0) -> Record:
    """A WALL as the snapshot gives it: a centre line, a stroke of 5 cm — and the
    built world makes 30 cm of masonry of it (``builders/walls.py``)."""
    half = length / 2.0
    return record(item_id, "WALL", east, north, rect(length, WALL_STROKE), height=height,
                  colour=GREY, shape="polyline", path=((-half, 0.0), (half, 0.0)),
                  width=WALL_STROKE)


# ── picking (gate G1) ────────────────────────────────────────────────────────

PICK_VIEW = (960, 600)
PICK_PLANS = {"bench_small": "bench_small.ogp", "edge_cases": "scene3d_edge_cases.ogp"}
#: Screen points tried per plan, in seeded order (the judge takes the first twenty
#: that are no edge case of rasterisation, plus the named ones).
PICK_CANDIDATES = 400
PICK_SEED = 385_108


def pick_camera(width_cm: float, depth_cm: float, top_cm: float) -> PerspectiveShot:
    """The orbit camera's own framing of the plan (``core.scene3d.camera.frame_pose``),
    tilted low enough that the upper part of the view is sky."""
    pose = frame_pose(Bounds.of_plan(width_cm, depth_cm, top_cm), PICK_VIEW[0] / PICK_VIEW[1],
                      pitch_deg=16.0)
    closer = CameraPose(pose.target, pose.distance_cm * 0.8, pose.yaw_deg, pose.pitch_deg)
    return PerspectiveShot(eye=closer.eye, target=closer.target_scene)


def seeded_points(count: int = PICK_CANDIDATES, seed: int = PICK_SEED,
                  view: tuple[int, int] = PICK_VIEW) -> list[tuple[float, float]]:
    """``count`` view positions, uniform over the view, from a fixed seed."""
    import random

    rng = random.Random(seed)
    return [(round(rng.uniform(4.0, view[0] - 4.0), 2), round(rng.uniform(4.0, view[1] - 4.0), 2))
            for _ in range(count)]


def load_plan(name: str) -> tuple[Any, Any, Any]:
    """A fixture plan in a real scene + a view (its undo stack) + the manager."""
    from open_garden_planner.core import ProjectManager
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.canvas_view import CanvasView

    scene = CanvasScene()
    manager = ProjectManager()
    manager.load(scene, PLANS / PICK_PLANS[name])
    view = CanvasView(scene)
    view.set_snap_enabled(False)
    return scene, view, manager


def plan_records(scene: Any) -> dict[str, Record]:
    from open_garden_planner.ui.view3d.snapshot import snapshot_records

    return snapshot_records(scene, JUNE)


def plan_top(records: Mapping[str, Record]) -> float:
    return max((r.transform.base_cm + r.height_cm for r in records.values()
                if r.height_cm is not None), default=0.0)


# ── the engine side ──────────────────────────────────────────────────────────


def _sink(host: Any) -> Any:
    from open_garden_planner.ui.view3d.quick3d.adapter import Quick3DSink

    return Quick3DSink(host)


def _ring_counters(host: Any) -> dict[str, Any]:
    from open_garden_planner.ui.view3d.quick3d.geometry import BufferGeometry, ReloadableGeometry

    return {"ring_loads": host.ring_loads, "reloadable_loads": int(ReloadableGeometry.loads),
            "ring_geometries": int(ReloadableGeometry.created),
            "model_uploads": int(BufferGeometry.uploads), "parts": host.part_count(),
            "engine_parts": len(host.engine_parts()),
            "ring_visible": bool(host.scene_property("selectionVisible"))}


def scenario_ring(ctx: Context) -> dict[str, Any]:
    """Gate G4: the ring where the footprint is — top-down (the truth view), then one
    perspective look at the plant on its bed; and what a selection change costs the
    engine in objects (the counters around every step)."""
    host = ctx.shown_host(RING_VIEW)
    out: dict[str, Any] = {"steps": []}
    try:
        sink = _sink(host)
        sync = SceneSync(sink, production_registry())
        records = ring_records()
        sync.apply(records, sun=HIGH_SUN, ground=RING_GROUND)
        host.set_shot(RING_TOP)

        def step(name: str, ids: tuple[str, ...]) -> None:
            before = _ring_counters(host)
            sent = sync.set_selection(ids)
            row = ctx.save(f"ring_{name}", host.grab(f"ring_{name}"))
            row.update({"name": name, "ids": list(ids), "sent": sent, "before": before,
                        "after": _ring_counters(host), "sink_ring_uploads": sink.ring_uploads,
                        "sink_geometry_uploads": sink.geometry_uploads})
            out["steps"].append(row)
            _pump(10)

        step("none", ())
        step("all", RING_SELECTED)
        for item_id in RING_SELECTED:
            step(item_id, (item_id,))
        step("cleared", ())
        # twenty more changes: what steady use creates and destroys
        before = _ring_counters(host)
        for index in range(20):
            sync.set_selection(RING_SELECTED[: 1 + index % 4])
            host.grab("ring_steady")
        out["steady"] = {"before": before, "after": _ring_counters(host)}
        # a selected item that moves takes its ring along, in the move's transaction
        sync.set_selection(("box",))
        moved = {**records, "box": record("box", "GARAGE_SHED", -300.0, 150.0, rect(220.0, 140.0),
                                         height=120.0, colour=RED, rotation=70.0,
                                         shape="rectangle")}
        before = _ring_counters(host)
        sync.apply(moved)
        row = ctx.save("ring_moved", host.grab("ring_moved"))
        row.update({"before": before, "after": _ring_counters(host)})
        out["moved"] = row
        sync.apply(records)
        # the perspective look at the plant on the bed
        sync.set_selection(("plant",))
        host.set_shot(RING_PERSPECTIVE)
        out["perspective"] = ctx.save("ring_perspective", host.grab("ring_perspective"))
        sync.set_selection(())
        out["perspective_none"] = ctx.save("ring_perspective_none",
                                           host.grab("ring_perspective_none"))
        out["view_size"] = list(host.view_size())
        out["state_equals_expected"] = sink.state() == sync.expected_state()
        return out
    finally:
        host.close()
        _pump()


def scenario_ring_depth(ctx: Context) -> dict[str, Any]:
    """Why the ring is drawn in front (``ringDepthScale``): a wall is built 30 cm thick
    on a 5 cm stroke, so a depth-tested ring along the stroke lies INSIDE the masonry.
    The same frame twice, from above: the shipped scale, and 1.0 (depth-tested)."""
    host = ctx.shown_host(RING_VIEW)
    out: dict[str, Any] = {}
    try:
        sync = SceneSync(_sink(host), production_registry())
        sync.apply({"wall": wall_record()}, sun=HIGH_SUN, ground=RING_GROUND)
        sync.set_selection(("wall",))
        host.set_shot(RING_TOP)
        out["shipped_scale"] = float(host.scene_property("ringDepthScale"))
        out["in_front"] = ctx.save("ring_depth_in_front", host.grab("ring_depth_in_front"))
        host._root.setProperty("ringDepthScale", 1.0)  # noqa: SLF001 - the control of this probe
        out["depth_tested"] = ctx.save("ring_depth_tested", host.grab("ring_depth_tested"))
        return out
    finally:
        host.close()
        _pump()


def scenario_pick(ctx: Context) -> dict[str, Any]:
    """Gate G1: the engine's answer at every seeded view position (and at the judge's
    named ones), for both plans, from the orbit camera's own framing."""
    out: dict[str, Any] = {}
    for name in PICK_PLANS:
        scene, _view, _manager = load_plan(name)
        records = plan_records(scene)
        shot = pick_camera(float(scene.width_cm), float(scene.height_cm), plan_top(records))
        host = ctx.shown_host(PICK_VIEW)
        try:
            sink = _sink(host)
            sync = SceneSync(sink, production_registry())
            sync.apply(records, sun=HIGH_SUN,
                       ground=GroundSpec(float(scene.width_cm), float(scene.height_cm)))
            host.set_shot(shot)
            frame = ctx.save(f"pick_{name}", host.grab(f"pick_{name}"))
            points = seeded_points() + named_points(name, records, shot)
            hits = []
            started = time.perf_counter()
            for x, y in points:
                hit = host.pick(x, y)
                hits.append(None if hit is None else {
                    "id": hit.item_id, "part": hit.part_index, "point": list(hit.point)})
            out[name] = {
                "frame": frame, "points": [list(p) for p in points], "hits": hits,
                "ms_per_pick": round((time.perf_counter() - started) * 1000.0 / len(points), 4),
                "view_size": list(host.view_size()), "records": len(records),
                "models": host.part_count(), "build_failures": sorted(sync.failures),
                "shot": {"eye": list(shot.eye), "target": list(shot.target), "fov": shot.fov_deg},
            }
        finally:
            host.close()
            _pump()
    return out


def named_points(name: str, records: Mapping[str, Record],
                 shot: PerspectiveShot) -> list[tuple[float, float]]:
    """The view positions gate G1 names — computed by the JUDGE's own projection, here
    only so that the engine is asked at them too (``pick_targets`` in the test module
    recomputes them and checks they are the same)."""
    from tests.integration.view3d_selection_support import named_pick_points

    return [point for _label, point in named_pick_points(name, records, shot)]


SCENARIOS: dict[str, Callable[[Context], dict[str, Any]]] = {
    "ring": scenario_ring,
    "ring_depth": scenario_ring_depth,
    "pick": scenario_pick,
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--qml-dir", type=Path, default=None)
    parser.add_argument("--watchdog-s", type=float, default=900.0)
    args = parser.parse_args(argv)
    wanted = args.scenarios.split(",")
    unknown = [name for name in wanted if name not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios {unknown}; choose from {sorted(SCENARIOS)}")
    args.out.mkdir(parents=True, exist_ok=True)
    _isolate_app_data(args.out)  # before anything of the app can ask where its data is
    _isolate_settings()          # before anything of the app can build a settings store

    from PyQt6.QtCore import QT_VERSION_STR

    started = time.perf_counter()
    from open_garden_planner.ui.view3d.quick3d import offscreen

    log = offscreen.RenderLog(args.out / "render.log")
    result: dict[str, Any] = {
        "status": "running", "platform": platform.platform(), "python": sys.version.split()[0],
        "qt": QT_VERSION_STR,
        "env": {key: os.environ.get(key) for key in (
            "QT_QPA_PLATFORM", "QSG_RHI_BACKEND", "QSG_RHI_PREFER_SOFTWARE_RENDERER", "APPDATA")},
        "scenarios": {},
    }

    def write() -> None:
        (args.out / "result.json").write_text(json.dumps(result, indent=1, default=str),
                                              encoding="utf-8")

    code = 0
    try:
        with offscreen.watchdog(args.watchdog_s, log, what="the selection driver", supervised=True):
            offscreen.ensure_application()
            context = Context(args.out, log, args.qml_dir.resolve() if args.qml_dir else None)
            log("start", scenarios=args.scenarios, qt=QT_VERSION_STR, platform=result["platform"])
            for name in wanted:
                log("scenario", name=name)
                print(f"OGPQT|scenario|{name}", file=sys.stderr, flush=True)
                t0 = time.perf_counter()
                data = SCENARIOS[name](context)
                data["seconds"] = round(time.perf_counter() - t0, 2)
                result["scenarios"][name] = data
                write()
            print("OGPQT|scenario|", file=sys.stderr, flush=True)
        result["status"] = "ok"
    except Exception as error:  # noqa: BLE001 - a driver: record it, never lose it
        result["status"] = "error"
        result["error"] = f"{type(error).__name__}: {error}"
        result["traceback"] = traceback.format_exc()
        log("error", error=result["error"])
        code = QT_ERRORS_EXIT if type(error).__name__ == "RenderError" else 2
    finally:
        result["total_s"] = round(time.perf_counter() - started, 2)
        write()
        log("done", status=result["status"], total_s=result["total_s"])
        log.close()
        with contextlib.suppress(Exception):
            _clear_settings()
    return code


_ = statistics  # used by scenarios added below


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
