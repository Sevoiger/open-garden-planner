"""Measure the work bound of the 3D pipeline's outlines (Phase 17 L1.1, #385).

Everything one canvas item's outline needs on its way into the 3D view — the
clipper, sorting its rings, cutting every region into triangles — is paid for in
work units out of ONE budget of ``MAX_REGION_WORK``, each step before it runs,
and what the budget does not cover is refused (``core/scene3d/polygon.py``,
``ui/view3d/snapshot.py``). A unit is a count, so the verdict is the same on
every machine; what a unit costs in TIME is a measurement, and this script is
where the figures in the code's comments, ADR-056 and sections 8.11 and 8.27.1
come from. Run it from the repo root:

    venv/Scripts/python.exe scripts/measure_region_work.py
    venv/Scripts/python.exe scripts/measure_region_work.py --quick     # the cuts only

It prints three tables.

1. CUTS. For four families of regions the largest member the bound admits: its
   size, its work (and the least the sizes alone promise), the time the cut
   took, the nanoseconds per unit — and the first member that is refused,
   whether that is decided from its size alone or while it is cut.
2. OUTLINES. The same for whole outlines, through the snapshot's own functions:
   strokes (a closed fence along a traced outline, a ladder and a mesh drawn as
   one stroke, a wall traced finer than it is wide, a line whose every segment
   lies level with every other) and a drawn outline that crosses itself.
3. REFUSALS. Outlines built to cost the clipper as much as they can: what
   saying no costs — in units, which are bounded, and in time. For each, what
   the clipper would have taken had it been asked (the reason the price exists).

The price of a step is right when a unit CHARGED takes no longer than a unit of
the reference (a plain traced outline, table 1): compare the last columns. The
time columns are this machine at this moment — quote them with their
conditions. The unit columns are exact.

Imports ``ui.view3d.snapshot`` (and with it PyQt6); no window, no application,
no plan. It isolates itself all the same (``scripts/script_isolation.py``): the
profile before the app is imported, the settings names first thing in ``main()``.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import script_isolation  # noqa: E402  (before the app is imported)

if __name__ == "__main__":  # run as a script: nothing from here on may reach the user's own stores
    script_isolation.isolate_profile()

from open_garden_planner.core.scene3d import polygon  # noqa: E402
from open_garden_planner.core.scene3d.polygon import (  # noqa: E402
    Region,
    RegionTooLargeError,
    Ring,
    WorkBudget,
    ccw,
    cut_work,
    exceeds_work_bound,
    region_work,
)
from open_garden_planner.core.shadow_geometry import polyline_footprint  # noqa: E402
from open_garden_planner.ui.view3d import snapshot  # noqa: E402

Path2 = tuple[tuple[float, float], ...]


def _ring(points: list[tuple[float, float]]) -> Ring:
    return tuple((round(x, 7), round(y, 7)) for x, y in points)


# ── regions ───────────────────────────────────────────────────────────────────


def traced(vertices: int) -> Region:
    """A circle of 30 m traced by hand: every vertex a little off the line."""
    rng = random.Random(vertices)
    wobble = 0.15 * 2.0 * math.pi * 3000.0 / vertices
    return _ring([((3000.0 + rng.uniform(-wobble, wobble)) * math.cos(2.0 * math.pi * k / vertices),
                   (3000.0 + rng.uniform(-wobble, wobble)) * math.sin(2.0 * math.pi * k / vertices))
                  for k in range(vertices)]), ()


def comb(teeth: int) -> Region:
    points = [(0.0, 0.0)]
    x = 0.0
    for _ in range(teeth):
        points += [(x, 200.0), (x + 5.0, 200.0), (x + 5.0, 10.0), (x + 10.0, 10.0)]
        x += 10.0
    points += [(x, 200.0), (x + 5.0, 200.0), (x + 5.0, 0.0)]
    return ccw(_ring(points)), ()


def spiral(turns: int) -> Region:
    per_turn = 6 * turns
    outer, inner = [], []
    for k in range(turns * per_turn + 1):
        angle = 2.0 * math.pi * k / per_turn
        radius = 50.0 + 60.0 * k / per_turn
        outer.append(((radius + 20.0) * math.cos(angle), (radius + 20.0) * math.sin(angle)))
        inner.append((radius * math.cos(angle), radius * math.sin(angle)))
    return ccw(_ring(outer + inner[::-1])), ()


def hidden_bridges(dense: int) -> Region:
    """Small holes whose NEAREST boundary vertices all lie behind a long thin one."""
    small, height = max(dense // 100, 2), 7000.0
    outer = ((-4000.0, -500.0), (4000.0, -500.0), (4000.0, height + 500.0), (-4000.0, height + 500.0))
    right = [(1.0 + (0.3 if k % 2 else 0.0), height * k / dense) for k in range(dense + 1)]
    holes: list[Ring] = [((0.0, 0.0), (0.0, height), *right[::-1])]
    for k in range(small):
        y = height * (k + 0.5) / small
        holes.append(((-3.0, y), (-6.0, y - 1.0), (-6.0, y + 1.0)))
    return outer, tuple(holes)


CUTS: tuple[tuple[str, str, Callable[[int], Region], int, int], ...] = (
    ("a plain traced outline", "vertices", traced, 256, 20000),
    ("a comb", "teeth", comb, 64, 5000),
    ("a spiral", "turns", spiral, 3, 60),
    ("a region that hides its bridges", "vertices on the long hole", hidden_bridges, 200, 20000),
)


# ── outlines ──────────────────────────────────────────────────────────────────


def closed_fence(points: int) -> tuple[Path2, float]:
    """A 10 cm fence along a traced outline, closed: one outer ring, one hole."""
    rng = random.Random(points)
    radius, wobble = 8.0 * points, 0.3 * 8.0 * 2.0 * math.pi
    path = [(round((radius + rng.uniform(-wobble, wobble)) * math.cos(2.0 * math.pi * k / points), 3),
             round((radius + rng.uniform(-wobble, wobble)) * math.sin(2.0 * math.pi * k / points), 3))
            for k in range(points)]
    return (*path, path[0]), 10.0


def ladder(pairs: int) -> tuple[Path2, float]:
    """Two rails and a rung every 40 cm, drawn without lifting the pen, 6 cm wide
    (in pairs of rungs: the pen ends where it began)."""
    path = [(0.0, 0.0)]
    for k in range(2 * pairs):
        x = 40.0 * k
        path += ([(x, 100.0), (x + 40.0, 100.0), (x + 40.0, 0.0), (x, 0.0)] if k % 2 == 0 else
                 [(x + 40.0, 0.0), (x + 40.0, 100.0), (x, 100.0)])
    return (*dict.fromkeys(path), (0.0, 0.0)), 6.0


def mesh(cells: int) -> tuple[Path2, float]:
    """A square mesh of ``cells`` x ``cells`` openings, drawn as one stroke, 4 cm wide."""
    path: list[tuple[float, float]] = []
    for row in range(cells + 1):
        line = [(column * 50.0, row * 50.0) for column in range(cells + 1)]
        path += line if row % 2 == 0 else line[::-1]
    for column in range(cells + 1):
        line = [(column * 50.0, row * 50.0) for row in range(cells + 1)]
        path += line if abs(line[0][1] - path[-1][1]) < abs(line[-1][1] - path[-1][1]) else line[::-1]
    return tuple(path), 4.0


def fine_wall(points: int) -> tuple[Path2, float]:
    """A wall 30 cm wide traced with a point every 2 cm — finer than it is wide."""
    return tuple((round(3000.0 * math.cos(k * 2.0 / 3000.0), 3), round(3000.0 * math.sin(k * 2.0 / 3000.0), 3))
                 for k in range(points)), 30.0


def level_line(points: int) -> tuple[Path2, float]:
    """A fence running east–west, a point every 50 cm, each a few centimetres off
    the line: every segment lies level with every other — the clipper's sweep
    carries them all at once."""
    rng = random.Random(points)
    return tuple((k * 50.0, round(rng.uniform(-3.0, 3.0), 3)) for k in range(points)), 10.0


STROKES: tuple[tuple[str, str, Callable[[int], tuple[Path2, float]], int, int], ...] = (
    ("a closed fence along a traced outline", "points", closed_fence, 50, 8000),
    ("the same fence, every segment level with every other", "points", level_line, 50, 8000),
    ("a ladder drawn as one stroke", "pairs of rungs", ladder, 3, 800),
    ("a square mesh drawn as one stroke", "cells a side", mesh, 2, 60),
    ("a wall traced finer than it is wide", "points", fine_wall, 50, 8000),
)


def crossed(vertices: int) -> Path2:
    """A traced outline with two of its vertices swapped: it crosses itself a few times."""
    ring = list(traced(vertices)[0])
    ring[10], ring[vertices // 2] = ring[vertices // 2], ring[10]
    return tuple(ring)


def scribble(points: int) -> Path2:
    """Points all over a plan of 200 m: every segment crosses about a quarter of the others."""
    rng = random.Random(points)
    return tuple((round(rng.uniform(0.0, 20000.0), 3), round(rng.uniform(0.0, 20000.0), 3))
                 for _ in range(points))


def standing_teeth(points: int) -> Path2:
    """A zigzag whose teeth stand side by side, 30 cm apart: nothing crosses, and a
    sweep line carries every tooth at once."""
    return tuple((k * 30.0, 1000.0 + k * 0.37 if k % 2 else -k * 0.41) for k in range(points))


def tight_zigzag(points: int) -> Path2:
    """Teeth half a centimetre apart under a stroke of 10: every stroke overlaps its neighbours."""
    return tuple((k * 0.5, 1000.0 if k % 2 else 0.0) for k in range(points))


# ── measuring ─────────────────────────────────────────────────────────────────


def _forget() -> None:
    for remembered in (polygon._examined, polygon.ring_area, snapshot._polyline_regions,
                       snapshot._polygon_regions):
        remembered.cache_clear()


def _cut(region: Region) -> tuple[int | None, float, str]:
    """``(work or None, milliseconds, how it ended)`` of one cut."""
    outer, holes = region
    start = time.perf_counter()
    try:
        work = cut_work(outer, holes)
    except RegionTooLargeError:
        took = (time.perf_counter() - start) * 1e3
        return None, took, "from its size alone" if exceeds_work_bound(outer, holes) else "while it was cut"
    return work, (time.perf_counter() - start) * 1e3, "cut"


def _outline(run: Callable[[WorkBudget], object]) -> tuple[int | None, int, float]:
    """``(work or None, units charged, milliseconds)`` of one outline, nothing remembered."""
    _forget()
    budget = WorkBudget()
    start = time.perf_counter()
    try:
        run(budget)
    except RegionTooLargeError:
        return None, min(budget.spent, budget.budget), (time.perf_counter() - start) * 1e3
    return budget.spent, budget.spent, (time.perf_counter() - start) * 1e3


def _stroke(made: tuple[Path2, float]) -> Callable[[WorkBudget], object]:
    path, width = made
    return lambda budget: snapshot._stroke_regions(path, width, budget)


def _polygon(ring: Path2) -> Callable[[WorkBudget], object]:
    return lambda budget: snapshot._outline_regions(ring, budget)


def _largest(fits: Callable[[int], bool], low: int, high: int) -> tuple[int, int]:
    assert fits(low), f"even {low} is refused"
    while high - low > 1:
        middle = (low + high) // 2
        if fits(middle):
            low = middle
        else:
            high = middle
    return low, high


def _size(region: Region) -> str:
    outer, holes = region
    vertices = len(outer) + sum(len(hole) for hole in holes)
    return f"{vertices:,} vertices, {len(holes)} hole{'s' if len(holes) != 1 else ''}"


def measure_cut(label: str, unit: str, make: Callable[[int], Region], low: int, high: int) -> float:
    low, high = _largest(lambda n: _cut(make(n))[0] is not None, low, high)
    region = make(low)
    work, took, _how = min((_cut(region) for _ in range(3)), key=lambda result: result[1])
    assert work is not None
    print(f"{label}\n    fits:    {low:,} {unit} ({_size(region)}): {work / 1e6:6.1f} M units "
          f"(the sizes alone promise {region_work(*region) / 1e6:6.1f} M), {took:6.1f} ms, "
          f"{took * 1e6 / work:4.2f} ns per unit")
    refused = make(high)
    _none, took_no, how = min((_cut(refused) for _ in range(3)), key=lambda result: result[1])
    print(f"    refused: {high:,} {unit} ({_size(refused)}) {how}, in {took_no:7.3f} ms")
    return took * 1e6 / work


def measure_outline(label: str, unit: str, run_of: Callable[[int], Callable[[WorkBudget], object]],
                    low: int, high: int) -> float:
    low, high = _largest(lambda n: _outline(run_of(n))[0] is not None, low, high)
    work, _charged, took = min((_outline(run_of(low)) for _ in range(3)), key=lambda result: result[2])
    assert work is not None
    print(f"{label}\n    fits:    {low:,} {unit}: {work / 1e6:6.1f} M units, {took:6.1f} ms, "
          f"{took * 1e6 / work:4.2f} ns per unit charged")
    _none, charged, took_no = min((_outline(run_of(high)) for _ in range(3)), key=lambda result: result[2])
    print(f"    refused: {high:,} {unit}, after {charged / 1e6:6.1f} M units, in {took_no:7.3f} ms")
    return took * 1e6 / work


def measure_refusal(label: str, run: Callable[[WorkBudget], object],
                    clipper: Callable[[], object] | None) -> None:
    work, charged, took = _outline(run)
    verdict = "REFUSED" if work is None else "built  "
    asked = ""
    if clipper is not None:
        start = time.perf_counter()
        clipper()
        asked = f"; the clipper alone, had it been asked: {(time.perf_counter() - start) * 1e3:9.1f} ms"
    print(f"    {verdict} {label}: {charged / 1e6:6.1f} M units charged, {took:8.3f} ms{asked}")


def main(argv: list[str] | None = None) -> int:
    script_isolation.isolate()
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--quick", action="store_true", help="the cuts only")
    args = parser.parse_args(argv)
    print(f"MAX_REGION_WORK = {polygon.MAX_REGION_WORK:,} work units; MAX_ITEM_VERTICES = "
          f"{polygon.MAX_ITEM_VERTICES:,}; Python {sys.version.split()[0]}\n\n1. CUTS\n")
    rates = [measure_cut(*family) for family in CUTS]
    print(f"\na unit took {min(rates):.2f} to {max(rates):.2f} ns on this machine, now; at that rate "
          f"the bound is {polygon.MAX_REGION_WORK * min(rates) / 1e9:.2f} to "
          f"{polygon.MAX_REGION_WORK * max(rates) / 1e9:.2f} s")
    if args.quick:
        return 0
    print("\n2. OUTLINES (everything the outline needs, out of one budget)\n")
    charged = [measure_outline(label, unit, lambda n, make=make: _stroke(make(n)), low, high)
               for label, unit, make, low, high in STROKES]
    charged.append(measure_outline("a drawn outline that crosses itself", "vertices",
                                   lambda n: _polygon(crossed(n)), 100, 8000))
    print(f"\na unit CHARGED took {min(charged):.2f} to {max(charged):.2f} ns (a price is right when "
          f"this does not exceed the {max(rates):.2f} ns of the cuts)")
    print("\n3. REFUSALS (outlines built to cost the clipper as much as they can)\n")
    for points in (400, 800, 1600):
        path = scribble(points)
        measure_refusal(f"a stroke scribbled over {points:,} points", _stroke((path, 10.0)),
                        lambda path=path: polyline_footprint(list(path), 10.0))
    for points in (2000, 4000):
        path = standing_teeth(points)
        measure_refusal(f"a stroke of {points:,} teeth standing side by side", _stroke((path, 10.0)),
                        lambda path=path: polyline_footprint(list(path), 10.0))
    path = tight_zigzag(1600)
    measure_refusal("a stroke of 1,600 teeth half a centimetre apart", _stroke((path, 10.0)),
                    lambda path=path: polyline_footprint(list(path), 10.0))
    path = tuple((k * 20.0, (k % 5) * 40.0) for k in range(16_000))
    measure_refusal("a stroke of 16,000 points", _stroke((path, 10.0)), None)
    for vertices in (200, 400, 800, 1600):
        ring = scribble(vertices)
        grid = [snapshot._to_grid(ring)]
        measure_refusal(f"an outline scribbled over {vertices:,} vertices", _polygon(ring),
                        (lambda grid=grid: snapshot._clip_tree(grid, 1, band=grid)) if vertices <= 400 else None)
    ring = traced(10_000)[0]
    measure_refusal("a plain outline of 10,000 vertices", _polygon(ring), None)
    print("\n(an item of more than MAX_ITEM_VERTICES vertices is refused before one of them is read: "
          "one comparison)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
