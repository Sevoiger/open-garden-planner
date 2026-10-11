"""Generate the committed 3D edge-case plan (Phase 17 L1.1, fix round 1, #385).

``tests/fixtures/plans/scene3d_edge_cases.ogp`` holds the shapes the two bench
plans do not contain and the first scene contract therefore got wrong without a
single gate noticing — above all a fence that closes on itself, whose stroke is
an outer ring and a HOLE. One item per case, on a 40 x 30 m canvas:

* a HOUSE turned by 25 degrees, with its roof ridge item (ADR-046);
* an ellipse (a pond), turned by 40 degrees;
* a closed-loop fence, 10 m x 8 m, 10 cm wide — outer ring + hole;
* a nearly closed one (a 2 cm gap, narrower than the stroke) — also a hole;
* an open U — no hole;
* a raised bed turned by 30 degrees, with two planted children;
* a round container with a plant;
* a polygon with a concave corner (an L-shaped hedge).

``tests/integration/test_scene3d_edge_plan.py`` runs the pipeline's gates over
it (snapshot stability, mesh validity and plan area, transform ∘ local footprint
== the 2D footprint) and pins that regenerating reproduces the committed file.
It is written through the real ``ProjectManager`` serializer with seeded ids,
exactly like ``scripts/make_bench_plans.py``, whose plumbing it reuses — and
which it never modifies: the bench plans stay byte for byte what they were.

Usage::

    venv/bin/python scripts/make_scene3d_edge_plan.py           # (re)write the fixture
    venv/bin/python scripts/make_scene3d_edge_plan.py --check   # verify it is current
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import make_bench_plans as bench  # noqa: E402  (sets up sys.path for the package and Qt's platform)
from PyQt6.QtWidgets import QApplication  # noqa: E402

from open_garden_planner.core.object_types import ObjectType, PathFenceStyle  # noqa: E402
from open_garden_planner.ui.canvas.geometry_apply import apply_rotation  # noqa: E402
from open_garden_planner.ui.canvas.items import EllipseItem  # noqa: E402

NAME = "scene3d_edge_cases.ogp"
FENCE_WIDTH_CM = 10.0
HOUSE_TURN_DEG = 25.0
POND_TURN_DEG = 40.0
BED_TURN_DEG = 30.0

#: name → centre line; each is the 10 m x 8 m rectangle, shifted to its place
FENCES: dict[str, list[tuple[float, float]]] = {
    "Closed fence": [(200, 200), (1200, 200), (1200, 1000), (200, 1000), (200, 200)],
    "Nearly closed fence": [(1500, 202), (1500, 1000), (2500, 1000), (2500, 200), (1500, 200)],
    "Open U fence": [(2800, 1000), (2800, 200), (3800, 200), (3800, 1000)],
}


def _turned(point: tuple[float, float], centre: tuple[float, float], degrees: float) -> tuple[float, float]:
    """``point`` turned counter-clockwise about ``centre`` (the canvas is Y-up)."""
    angle = math.radians(degrees)
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    return (centre[0] + dx * math.cos(angle) - dy * math.sin(angle),
            centre[1] + dx * math.sin(angle) + dy * math.cos(angle))


def build_edge_cases() -> bench.PlanBuilder:
    b = bench.PlanBuilder(4000.0, 3000.0, seed="ogp-scene3d-edge-cases-v1")
    T = ObjectType

    house = b.house([(300, 2000), (1200, 2000), (1200, 2540), (300, 2540)])
    apply_rotation(house, HOUSE_TURN_DEG)  # PolygonItem re-derives the ridge item (ADR-046)

    pond = b._add(EllipseItem(1700.0, 2100.0, 600.0, 300.0, object_type=T.POND_POOL, name="Pond"))
    apply_rotation(pond, POND_TURN_DEG)

    for name, centre_line in FENCES.items():
        # No style preset: the loader lets a preset override a saved stroke width
        # (``core/project.py`` restores it only for ``PathFenceStyle.NONE``), and
        # these three are about a stroke of exactly 10 cm.
        fence = b.polyline(centre_line, T.FENCE, PathFenceStyle.NONE, name)
        pen = fence.pen()
        pen.setWidthF(FENCE_WIDTH_CM)
        fence.setPen(pen)

    bed = b.rect(1700.0, 1300.0, 520.0, 120.0, T.RAISED_BED, "Turned raised bed")
    apply_rotation(bed, BED_TURN_DEG)
    centre = (1960.0, 1360.0)
    for offset, crop in ((-130.0, "Tomato"), (130.0, "Basil")):
        x, y = _turned((centre[0] + offset, centre[1]), centre, BED_TURN_DEG)
        b.plant(x, y, crop, T.PERENNIAL, parent=bed)

    pot = b.circle(2900.0, 1500.0, 30.0, T.CONTAINER_ROUND, "Pot")
    b.plant(2900.0, 1500.0, "Rosemary", T.PERENNIAL, spread_cm=45, height_cm=50, parent=pot)

    b.polygon([(3000, 2000), (3900, 2000), (3900, 2300), (3400, 2300), (3400, 2700), (3000, 2700)],
              T.HEDGE_POLYGON, "L-shaped hedge")
    return b


def main(argv: list[str] | None = None) -> int:
    bench._isolate_settings()
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="regenerate to a temp dir and compare with the committed fixture")
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841

    committed = bench.OUT_DIR / NAME
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / NAME
            bench._save(build_edge_cases(), fresh)
            current = committed.is_file() and bench._normalised(fresh) == bench._normalised(committed)
        if not current:
            print(f"STALE: {committed.relative_to(bench.REPO)} — rerun scripts/make_scene3d_edge_plan.py")
        print("scene3d edge-case plan: " + ("OK" if current else "FAIL"))
        return 0 if current else 1

    builder = build_edge_cases()
    bench._save(builder, committed)
    print(f"wrote {committed.relative_to(bench.REPO)}: {builder.count} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
