"""Measure the work bound of the 3D pipeline's polygon cutter (Phase 17 L1.1, #385).

``core/scene3d/polygon.py`` cuts every region of every scene record into
triangles and refuses a region whose cut would cost more than ``MAX_REGION_WORK``
work units. A unit is a count, so the verdict is the same on every machine; what
a unit costs in TIME is a measurement, and this script is where the figures in
the module's comments, ADR-054 and section 8.26.1 come from. Run it from the
repo root:

    venv/Scripts/python.exe scripts/measure_region_work.py
    venv/Scripts/python.exe scripts/measure_region_work.py --quick     # families only

For seven families of regions it finds the LARGEST member the bound admits and
prints its size, its work (and the least the sizes alone promise), the time the
cut took and the nanoseconds per unit; then the first member that is refused,
whether that is decided from its size alone or while it is cut, and how long the
refusal takes. The time column is this machine at this moment — quote it with
its conditions. The unit column is exact.

Families: a plain traced outline; a comb (the ear clipper's worst case: every
tooth waits for its neighbour); a spiral; a closed fence along a traced outline
(one hole); a ladder and a square mesh drawn as ONE stroke (a hole per cell);
and a region built to hide every nearby bridge (the bridge search's worst case).

Nothing here is Qt: only ``core.scene3d.polygon`` and the stroke offset of
``core.shadow_geometry``.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from open_garden_planner.core.scene3d import polygon  # noqa: E402
from open_garden_planner.core.scene3d.polygon import (  # noqa: E402
    Region,
    RegionTooLargeError,
    Ring,
    ccw,
    cut_work,
    exceeds_work_bound,
    group_rings,
    region_work,
)
from open_garden_planner.core.shadow_geometry import polyline_footprint  # noqa: E402


def _ring(points: list[tuple[float, float]]) -> Ring:
    return tuple((round(x, 7), round(y, 7)) for x, y in points)


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


def _stroke(path: list[tuple[float, float]], width: float) -> Region:
    ((outer, holes),) = group_rings([_ring(list(ring)) for ring in polyline_footprint(path, width)])
    return outer, holes


def closed_fence(points: int) -> Region:
    """A 10 cm fence along a traced outline, closed: one outer ring, one hole."""
    rng = random.Random(points)
    radius, wobble = 8.0 * points, 0.3 * 8.0 * 2.0 * math.pi
    path = [(round((radius + rng.uniform(-wobble, wobble)) * math.cos(2.0 * math.pi * k / points), 3),
             round((radius + rng.uniform(-wobble, wobble)) * math.sin(2.0 * math.pi * k / points), 3))
            for k in range(points)]
    return _stroke([*path, path[0]], 10.0)


def ladder(rungs: int) -> Region:
    """Two rails and a rung every 40 cm, drawn without lifting the pen, 6 cm wide."""
    path = [(0.0, 0.0)]
    for k in range(rungs):
        x = 40.0 * k
        path += ([(x, 100.0), (x + 40.0, 100.0), (x + 40.0, 0.0), (x, 0.0)] if k % 2 == 0 else
                 [(x + 40.0, 0.0), (x + 40.0, 100.0), (x, 100.0)])
    return _stroke([*dict.fromkeys(path), (0.0, 0.0)], 6.0)


def mesh(cells: int) -> Region:
    """A square mesh of ``cells`` x ``cells`` openings, drawn as one stroke, 4 cm wide."""
    path: list[tuple[float, float]] = []
    for row in range(cells + 1):
        line = [(column * 50.0, row * 50.0) for column in range(cells + 1)]
        path += line if row % 2 == 0 else line[::-1]
    for column in range(cells + 1):
        line = [(column * 50.0, row * 50.0) for row in range(cells + 1)]
        path += line if abs(line[0][1] - path[-1][1]) < abs(line[-1][1] - path[-1][1]) else line[::-1]
    return _stroke(path, 4.0)


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


FAMILIES: tuple[tuple[str, str, Callable[[int], Region], int, int], ...] = (
    ("a plain traced outline", "vertices", traced, 256, 20000),
    ("a comb", "teeth", comb, 64, 5000),
    ("a spiral", "turns", spiral, 3, 60),
    ("a closed fence along a traced outline", "points", closed_fence, 50, 8000),
    ("a ladder drawn as one stroke", "rungs", ladder, 6, 1500),
    ("a square mesh drawn as one stroke", "cells a side", mesh, 2, 60),
    ("a region that hides its bridges", "vertices on the long hole", hidden_bridges, 200, 20000),
)


def _try(region: Region) -> tuple[int | None, float, str]:
    """``(work or None, milliseconds, how it ended)`` of one cut."""
    outer, holes = region
    start = time.perf_counter()
    try:
        work = cut_work(outer, holes)
    except RegionTooLargeError:
        took = (time.perf_counter() - start) * 1e3
        return None, took, "from its size alone" if exceeds_work_bound(outer, holes) else "while it was cut"
    return work, (time.perf_counter() - start) * 1e3, "cut"


def _size(region: Region) -> str:
    outer, holes = region
    vertices = len(outer) + sum(len(hole) for hole in holes)
    return f"{vertices:,} vertices, {len(holes)} hole{'s' if len(holes) != 1 else ''}"


def measure(label: str, unit: str, make: Callable[[int], Region], low: int, high: int) -> list[float]:
    """Binary search for the largest member the bound admits; print it and its neighbour."""
    assert _try(make(low))[0] is not None, f"{label}: even {low} {unit} is refused"
    while high - low > 1:
        middle = (low + high) // 2
        if _try(make(middle))[0] is not None:
            low = middle
        else:
            high = middle
    region = make(low)
    work, took, _how = min((_try(region) for _ in range(3)), key=lambda result: result[1])
    assert work is not None
    floor = region_work(*region)
    print(f"{label}\n    fits:    {low:,} {unit} ({_size(region)}): {work / 1e6:6.1f} M units "
          f"(the sizes alone promise {floor / 1e6:6.1f} M), {took:6.1f} ms, {took * 1e6 / work:4.2f} ns per unit")
    refused = make(high)
    _none, took_no, how = min((_try(refused) for _ in range(3)), key=lambda result: result[1])
    print(f"    refused: {high:,} {unit} ({_size(refused)}) {how}, in {took_no:7.2f} ms")
    return [took * 1e6 / work]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--quick", action="store_true", help="skip the ten-times-larger refusals")
    args = parser.parse_args(argv)
    print(f"MAX_REGION_WORK = {polygon.MAX_REGION_WORK:,} work units; Python {sys.version.split()[0]}\n")
    rates: list[float] = []
    for label, unit, make, low, high in FAMILIES:
        rates += measure(label, unit, make, low, high)
    print(f"\na unit took {min(rates):.2f} to {max(rates):.2f} ns on this machine, now; at that rate the "
          f"bound is {polygon.MAX_REGION_WORK * min(rates) / 1e9:.2f} to "
          f"{polygon.MAX_REGION_WORK * max(rates) / 1e9:.2f} s of cutting")
    if not args.quick:
        print("\nfar beyond the bound — what saying no costs:")
        for label, make, size in (("a plain traced outline of 50,000 vertices", traced, 50000),
                                  ("a closed fence along 20,000 traced points", closed_fence, 20000),
                                  ("a ladder of 3,000 rungs", ladder, 3000)):
            start = time.perf_counter()
            region = make(size)
            made = (time.perf_counter() - start) * 1e3
            _none, took, how = _try(region)
            print(f"    {label} ({_size(region)}): refused {how} in {took:.3f} ms "
                  f"(making the rings took {made:,.0f} ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
