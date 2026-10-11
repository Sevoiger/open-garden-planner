"""Measure the 3D plant builder — the numbers its tests and documents state (Phase 17 L1.7, #385).

Every bound a plant test asserts, and every number §8.26.7 and the ADR-054 addendum "plant art v0"
quote, was MEASURED. This script is how; run it again after changing
``core/scene3d/builders/plants.py`` and move the numbers with the code.

The instruments are the unit tests' own (``tests/unit/test_scene3d_plants.py`` is loaded by path:
one definition of a side silhouette, of a height quantile, of the size matrix), so what is
measured here is exactly what is asserted there.

    venv/Scripts/python.exe scripts/measure_plant_art_3d.py truth            # built to the data?
    venv/Scripts/python.exe scripts/measure_plant_art_3d.py reach --ids 1000 # how far outside the canopy circle
    venv/Scripts/python.exe scripts/measure_plant_art_3d.py clones --ids 30  # how alike two ids are
    venv/Scripts/python.exe scripts/measure_plant_art_3d.py shapes           # triangles and bytes per shape
    venv/Scripts/python.exe scripts/measure_plant_art_3d.py scrub --days 180 # a date scrub, tick by tick

``truth``   per size group and archetype: the builder's own top before the fit, what the fit
            repaired (``FitReport``), where the foliage's mass is (z95 / z99), ground-flat triangles.
``reach``   the farthest vertex from the plant's axis, in canopy radii, over N ids × three
            proportions × three recipe forms per archetype (the bounding box is exact by the fit;
            this is the other half of the spread truth).
``clones``  side-silhouette IoU of every pair of N ids per recipe, as a distribution.
``shapes``  the largest plant per archetype and what a remembered shape costs in bytes.
``scrub``   both bench plans, the plan date moved day by day: plants that change per tick, shapes
            rebuilt per tick, milliseconds warm and cold — and the same in units of a reference
            workload timed alongside (a shared machine slows everything down together).

Not part of the suite. ``scrub`` needs Qt (it loads plans into a scene); the others need numpy only.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))   # this checkout's package, whatever else is installed
sys.path.insert(0, str(REPO))           # ... and its tests package

FIELDS = ("built_top", "z_scale", "xy_scale", "clamped_share", "clamped_reach", "pressed_share",
          "set_down", "centre_shift", "stretch")
PROPORTIONS = ((100.0, 50.0), (300.0, 40.0), (30.0, 120.0))
FORMS = ({}, {"leaf_scale": 1.35, "n_out": 10, "frilly": True},
         {"leaf_scale": 0.55, "n_out": 22, "narrow": True, "heart": False})


def load(relative: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def instruments() -> ModuleType:
    return load("tests/unit/test_scene3d_plants.py", "plant_test_instruments")


def truth(_args: argparse.Namespace) -> None:
    t = instruments()
    high: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(lambda: -1e9))
    low: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(lambda: 1e9))
    builds = 0

    def take(group: str, recipe: dict, seed: int, height: float, radius: float) -> None:
        nonlocal builds
        builds += 1
        parts = t.build_silhouette(recipe, seed, height, radius)
        report = t.fit_report(recipe, seed, height, radius)
        foliage = t.part(parts, t.PART_FOLIAGE).mesh
        z95, z99 = t.height_quantiles(foliage, (0.95, 0.99))
        values = {name: getattr(report, name) for name in FIELDS}
        values.update(z95=z95 / height, z99=z99 / height,
                      flat=float(t.ground_flat_triangles(foliage, height)))
        for key in ((group, recipe["a"]), (group, "ALL")):
            for name, value in values.items():
                high[key][name] = max(high[key][name], value)
                low[key][name] = min(low[key][name], value)

    for sprite in t.ALL_SPRITES:
        recipe = t.recipe_of(sprite)
        for label, (height, radius) in t.sizes_of(sprite).items():
            group = label if label in ("flat", "tall") else "bundled sizes"
            for seed in t.SEEDS:
                take(group, recipe, t.plant_seed(seed), height, radius)
    for archetype in sorted(t.ARCHETYPES):
        for height, radius in t.EXTREMES:
            for extra in t.EXTREME_RECIPES:
                for seed in (11, 12):
                    take("extremes", {"a": archetype, **extra}, seed, height, radius)
    print(f"{builds} builds")
    for group in ("bundled sizes", "flat", "tall", "extremes"):
        print(f"\n== {group} ==\n{'archetype':9s}  built top       xy scale  clamped  reach   pressed  "
              f"set down  shift   stretch         z95    z99    flat")
        for archetype in [*sorted(t.ARCHETYPES), "ALL"]:
            h, lo = high[(group, archetype)], low[(group, archetype)]
            print(f"{archetype:9s}  {lo['built_top']:.4f}..{h['built_top']:.4f}  {h['xy_scale']:.3f}     "
                  f"{100 * h['clamped_share']:.2f} %   {h['clamped_reach']:.3f}   {h['pressed_share']:.3f}    "
                  f"{h['set_down']:.3f}     {h['centre_shift']:.3f}   {lo['stretch']:.4f}..{h['stretch']:.4f}  "
                  f"{lo['z95']:.3f}  {lo['z99']:.3f}  {h['flat']:.0f}")


def reach(args: argparse.Namespace) -> None:
    t = instruments()
    print(f"{args.ids} ids x {len(PROPORTIONS)} proportions x {len(FORMS)} recipe forms per archetype")
    print(f"{'archetype':9s}  farthest vertex / canopy radius: max   99.9th percentile")
    overall = 0.0
    for archetype in sorted(t.ARCHETYPES):
        seen = []
        for form in FORMS:
            for height, radius in PROPORTIONS:
                for seed in range(args.ids):
                    parts = t.build_silhouette({"a": archetype, **form}, seed * 7919 + 13, height, radius)
                    seen.append(max(float(np.hypot(p.mesh.positions[:, 0], p.mesh.positions[:, 1]).max())
                                    for p in parts) / radius)
        values = np.array(seen)
        overall = max(overall, float(values.max()))
        print(f"{archetype:9s}  {values.max():.3f}   {np.percentile(values, 99.9):.3f}")
    print(f"{'ALL':9s}  {overall:.3f}")


def clones(args: argparse.Namespace) -> None:
    t = instruments()
    ids = [f"clone-{n}" for n in range(args.ids)]
    values: dict[str, list[float]] = defaultdict(list)
    worst: dict[str, tuple[float, str]] = {}
    for sprite in t.ALL_SPRITES:
        recipe = t.recipe_of(sprite)
        height, spread = t.MATURE[sprite]
        radius = spread / 2.0
        grid = np.array([t.side_silhouette(t.build_silhouette(recipe, t.plant_seed(seed), height, radius),
                                           height, radius).reshape(-1) for seed in ids], np.float64)
        shared = grid @ grid.T
        area = grid.sum(axis=1)
        pairs = (shared / (area[:, None] + area[None, :] - shared))[np.triu_indices(len(ids), 1)]
        values[recipe["a"]].extend(pairs.tolist())
        if pairs.max() > worst.get(recipe["a"], (0.0, ""))[0]:
            worst[recipe["a"]] = (float(pairs.max()), sprite.name)
    print(f"{args.ids} ids per recipe, at its mature size")
    print(f"{'archetype':9s}  pairs    max    p99    p95    median  above 0.97  above 0.95  most alike")
    rows = [*sorted(values.items()), ("ALL", [v for group in values.values() for v in group])]
    for archetype, group in rows:
        alike = np.array(group)
        print(f"{archetype:9s}  {len(alike):6d}  {alike.max():.3f}  {np.percentile(alike, 99):.3f}  "
              f"{np.percentile(alike, 95):.3f}  {np.median(alike):.3f}   {100 * (alike > 0.97).mean():6.2f} %    "
              f"{100 * (alike > 0.95).mean():6.2f} %   {worst.get(archetype, (0, ''))[1]}")


def shapes(_args: argparse.Namespace) -> None:
    t = instruments()
    triangles: dict[str, int] = defaultdict(int)
    held = []
    for sprite in t.ALL_SPRITES:
        recipe = t.recipe_of(sprite)
        for height, radius in t.sizes_of(sprite).values():
            for seed in t.SEEDS:
                count = t.triangles(t.build_silhouette(recipe, t.plant_seed(seed), height, radius))
                triangles[recipe["a"]] = max(triangles[recipe["a"]], count)
        height, spread = t.MATURE[sprite]
        held.append(t.shape_bytes(recipe, 1, height, spread / 2.0))
    print("largest plant per archetype (every recipe x six sizes x five ids), triangles:")
    print("  " + ", ".join(f"{a} {n:,}" for a, n in sorted(triangles.items(), key=lambda kv: -kv[1])))
    print(f"a remembered shape (123 recipes at mature size): min {min(held):,} bytes, "
          f"mean {sum(held) / len(held):,.0f}, max {max(held):,}")
    print(f"the cache holds {t.SHAPE_CACHE_SIZE} shapes: at most {t.SHAPE_CACHE_SIZE * max(held) / 1e6:.0f} MB, "
          f"{t.SHAPE_CACHE_SIZE * sum(held) / len(held) / 1e6:.0f} MB full of average plants")


def scrub(args: argparse.Namespace) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from open_garden_planner.core import ProjectManager
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.view3d.snapshot import snapshot_records

    budget = load("tests/perf/test_scene3d_plants_budget.py", "plant_budget_instruments")
    start = date.fromisoformat(args.start)
    days = [start + timedelta(days=n) for n in range(args.days + 1)]
    print(f"{args.days} daily ticks from {start}; reference unit {budget.unit_ms():.0f} ms right now")
    for name in sorted(budget.BUDGETS):
        scene = CanvasScene()
        ProjectManager().load(scene, budget.PLANS / name)
        took = time.perf_counter()
        snapshots = [budget.plants_of(snapshot_records(scene, day)) for day in days]
        snapshot_ms = (time.perf_counter() - took) * 1000.0 / len(days)
        print(f"\n{name}: {len(snapshots[0])} plants; snapshot_records {snapshot_ms:.1f} ms a day "
              f"(all records of the plan, not only plants)")
        for mode in ("warm", "cold"):
            unit = budget.unit_ms()
            budget.clear_shape_cache()
            sync = budget.SceneSync(budget.RecordingSink(), budget.plant_registry())
            sync.apply(snapshots[0])
            times, changed, built = [], [], []
            for records in snapshots[1:]:
                if mode == "cold":
                    budget.clear_shape_cache()
                misses, builds = budget.shape_cache_info()[1], sync.build_count
                began = time.perf_counter()
                sync.apply(records)
                times.append((time.perf_counter() - began) * 1000.0)
                changed.append(sync.build_count - builds)
                built.append(budget.shape_cache_info()[1] - misses)
            unit = statistics.median([unit, budget.unit_ms()])
            ordered = sorted(times)
            print(f"  {mode}: {statistics.mean(changed):5.1f} plants change per tick (max {max(changed)}), "
                  f"{statistics.mean(built):5.1f} shapes built per tick (max {max(built)}); "
                  f"median {statistics.median(times):6.1f} ms, p95 {ordered[int(0.95 * (len(ordered) - 1))]:6.1f}, "
                  f"max {ordered[-1]:6.1f} = {statistics.median(times) / unit:.3f} units of {unit:.0f} ms")
    del app


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("truth").set_defaults(run=truth)
    sub = commands.add_parser("reach")
    sub.add_argument("--ids", type=int, default=1000)
    sub.set_defaults(run=reach)
    sub = commands.add_parser("clones")
    sub.add_argument("--ids", type=int, default=30)
    sub.set_defaults(run=clones)
    commands.add_parser("shapes").set_defaults(run=shapes)
    sub = commands.add_parser("scrub")
    sub.add_argument("--days", type=int, default=180)
    sub.add_argument("--start", default="2026-04-01")
    sub.set_defaults(run=scrub)
    args = parser.parse_args()
    args.run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
